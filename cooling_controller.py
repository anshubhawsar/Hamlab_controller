"""
cooling_controller.py
=====================

Central controller that ties the ESP32 serial manager to the HAM Lab UI.

Responsibilities:

* Own a ``SerialManager`` instance.
* Maintain a single source of truth for the cooling system state
  (connection, port, inlet/outlet temperatures, pump status, mode).
* Log every temperature reading (with a timestamp) to a CSV file.
* Expose a clean, thread-safe control API used by the GUI:
  ``connect``, ``disconnect``, ``pump_on``, ``pump_off``, ``auto_mode``,
  ``send_status`` and ``refresh_ports``.
* Convert serial ``TEMP`` frames into state updates and notify the UI
  through callbacks.

The class is UI-agnostic: callbacks are invoked with plain strings/dicts so
the GUI layer decides how to render them.
"""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from serial_manager import SerialManager, ParsedFrame

#: CSV header written on the first row of every log file.
CSV_HEADER = ["timestamp", "inlet_temp_c", "outlet_temp_c", "pump_status"]

#: Default folder for temperature logs (relative to the project base dir).
LOGS_DIR_NAME = "logs"


def _resolve_logs_dir() -> str:
    """Return the absolute path of the temperature logs directory.

    The directory is created on demand (later in :meth:`_ensure_logs_dir`).
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), LOGS_DIR_NAME)


class CoolingController:
    """High-level controller for the ESP32 cooling system.

    Thread safety
    -------------
    State reads use the internal ``_lock``. Serial callbacks arrive from the
    ``SerialManager`` reader thread, so every mutation of shared state happens
    under the lock and every UI notification is forwarded through the caller's
    threading boundary (the GUI wrapper uses ``after(0, ...)``).

    Callbacks
    ---------
    * ``on_data(inlet_temp, outlet_temp, pump, mode, flow_rate, pwm)`` - invoked
      for every valid telemetry reading (from the serial reader thread).
    * ``on_connection_change(state, port)`` - invoked whenever the ESP32
      connects or disconnects.
    """

    def __init__(
        self,
        on_data: Optional[Callable[[float, float, str, str, float, int], None]] = None,
        on_connection_change: Optional[Callable[[str, Optional[str]], None]] = None,
    ) -> None:
        """Create the cooling controller.

        Args:
            on_data: Called with
                ``(inlet_c, outlet_c, pump, mode, flow_rate, pwm)``.
            on_connection_change: Called with ``("connected", port)`` or
                ``("disconnected", None)``.
        """
        self.on_data = on_data
        self.on_connection_change = on_connection_change

        self._lock = threading.RLock()

        # --- serial layer ---
        self.serial_manager = SerialManager(
            on_data=self._on_serial_data,
            on_connection_change=self._on_serial_connection,
        )

        # --- state ---
        self._connected = False
        self._port: Optional[str] = None
        self._inlet_temp: Optional[float] = None
        self._outlet_temp: Optional[float] = None
        self._pump: str = "OFF"
        self._mode: str = "MANUAL"  # AUTO / MANUAL
        self._last_pump_command: str = "OFF"
        self._flow_rate: float = 0.0
        self._pwm: int = 0

        # --- CSV ---
        self._csv_writer = None
        self._csv_file = None

    # ------------------------------------------------------------------
    # Public control API (used by the GUI)
    # ------------------------------------------------------------------
    def connect(self, port: Optional[str] = None) -> bool:
        """Connect to the ESP32.

        Args:
            port: Optional explicit port; auto-detected when omitted.

        Returns:
            True on success.
        """
        if not SerialManager.is_available():
            return False
        ok = self.serial_manager.connect(port)
        if ok and port:
            with self._lock:
                self._port = port
        return ok

    def disconnect(self) -> None:
        """Disconnect from the ESP32 and stop auto-reconnect."""
        self.serial_manager.disconnect()
        # Note: serial_manager.on_connection_change triggers the UI update.

    def pump_on(self) -> bool:
        """Switch the cooling pump ON (manual mode).

        Returns:
            True when the command was accepted by the serial layer.
        """
        with self._lock:
            self._mode = "MANUAL"
            self._last_pump_command = "ON"
        return self.serial_manager.send_command("ON")

    def pump_off(self) -> bool:
        """Switch the cooling pump OFF (manual mode).

        Returns:
            True when the command was accepted by the serial layer.
        """
        with self._lock:
            self._mode = "MANUAL"
            self._last_pump_command = "OFF"
        return self.serial_manager.send_command("OFF")

    def auto_mode(self) -> bool:
        """Enable the ESP32's automatic pump control.

        Returns:
            True when the command was accepted by the serial layer.
        """
        with self._lock:
            self._mode = "AUTO"
        return self.serial_manager.send_command("AUTO")

    def set_pwm(self, value: int) -> bool:
        """Set the manual pump speed (only relevant in manual mode).

        The firmware only applies ``PWM=<value>`` while the pump is ON, so this
        method first ensures the pump is running (sends ``ON``) and then applies
        the requested speed. This makes manual speed control work even when the
        pump was previously switched off.

        Args:
            value: PWM duty cycle in the range 0..255.

        Returns:
            True when every command was accepted by the serial layer.
        """
        value = int(value)
        if value < 0:
            value = 0
        if value > 255:
            value = 255
        with self._lock:
            self._mode = "MANUAL"
            self._last_pump_command = "ON"
        on_ok = self.serial_manager.send_command("ON")
        pwm_ok = self.serial_manager.send_command(f"PWM={value}")
        return on_ok and pwm_ok

    def send_status(self) -> bool:
        """Request a diagnostic status dump from the ESP32.

        Returns:
            True when the command was accepted by the serial layer.
        """
        return self.serial_manager.send_command("STATUS")

    def refresh_ports(self) -> List[str]:
        """Return the list of currently available COM ports."""
        return SerialManager.list_serial_ports()

    def start(self) -> None:
        """Start the automatic reconnect worker (call once after creation)."""
        self.serial_manager.start_auto_reconnect()

    def shutdown(self) -> None:
        """Stop threads, close the port and close the CSV log file."""
        self.serial_manager.shutdown()
        self._close_csv()

    # ------------------------------------------------------------------
    # State accessors (thread-safe)
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        """Return True when the ESP32 is currently connected."""
        return self.serial_manager.is_connected()

    def get_port(self) -> Optional[str]:
        """Return the active COM port name (or None)."""
        return self.serial_manager.get_port()

    def get_state(self) -> Dict[str, object]:
        """Return a snapshot of the whole cooling system state.

        Returns a dict with keys: ``connected``, ``port``, ``inlet_temp``,
        ``outlet_temp``, ``pump``, ``mode``, ``flow_rate``, ``pwm`` and
        ``alarm_status``.
        """
        with self._lock:
            return {
                "connected": self._connected,
                "port": self._port,
                "inlet_temp": self._inlet_temp,
                "outlet_temp": self._outlet_temp,
                "pump": self._pump,
                "mode": self._mode,
                "flow_rate": self._flow_rate,
                "pwm": self._pwm,
                "alarm_status": self.compute_alarm_status(self._outlet_temp),
            }

    @staticmethod
    def compute_alarm_status(outlet_temp: Optional[float]) -> str:
        """Return the alarm status based on the outlet temperature.

        Returns ``"HIGH_TEMP"`` when the outlet reaches/exceeds 45 °C,
        otherwise ``"NONE"``.
        """
        if outlet_temp is not None and outlet_temp >= 45.0:
            return "HIGH_TEMP"
        return "NONE"

    # ------------------------------------------------------------------
    # SerialManager callbacks (arrive from the serial reader thread)
    # ------------------------------------------------------------------
    def _on_serial_connection(self, state: str, port: Optional[str]) -> None:
        """Handle connection state changes coming from the serial manager."""
        with self._lock:
            self._connected = state == "connected"
            if self._connected:
                self._port = port
            else:
                # Keep last temperatures visible but mark pump as unknown-safe.
                self._pump = "OFF"
                self._flow_rate = 0.0
                self._pwm = 0
            changed_mode = self._mode

        if self.on_connection_change is not None:
            try:
                self.on_connection_change(state, port)
            except Exception:
                pass

    def _on_serial_data(self, frame: ParsedFrame) -> None:
        """Handle a parsed TEMP frame: update state and log to CSV.

        Args:
            frame: Dict with ``inlet_temp``, ``outlet_temp`` and ``pump``.
        """
        inlet = float(frame.get("inlet_temp", 0.0))
        outlet = float(frame.get("outlet_temp", 0.0))
        pump = str(frame.get("pump", "OFF"))
        flow_rate = float(frame.get("flow_rate", 0.0))
        pwm = int(frame.get("pwm", 0))

        with self._lock:
            self._inlet_temp = inlet
            self._outlet_temp = outlet
            self._pump = pump
            self._flow_rate = flow_rate
            self._pwm = pwm
            mode = self._mode

        # Log to CSV (never allow a logging failure to disturb the UI).
        try:
            self._log_reading(inlet, outlet, pump)
        except Exception:
            pass

        if self.on_data is not None:
            try:
                self.on_data(inlet, outlet, pump, mode, flow_rate, pwm)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # CSV logging
    # ------------------------------------------------------------------
    def _ensure_csv(self) -> None:
        """Open (or create) today's CSV log file and write the header.

        The file is named ``cooling_temps_YYYYMMDD.csv`` inside ``logs/``.
        """
        if self._csv_file is not None and not self._csv_file.closed:
            return

        logs_dir = _resolve_logs_dir()
        os.makedirs(logs_dir, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = os.path.join(logs_dir, f"cooling_temps_{today}.csv")

        file_exists = os.path.exists(path)
        self._csv_file = open(path, "a", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)

        if not file_exists or os.path.getsize(path) == 0:
            self._csv_writer.writerow(CSV_HEADER)
            self._csv_file.flush()

    def _log_reading(self, inlet: float, outlet: float, pump: str) -> None:
        """Append a single temperature reading to the CSV log."""
        self._ensure_csv()
        if self._csv_writer is None:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._csv_writer.writerow([timestamp, f"{inlet:.2f}", f"{outlet:.2f}", pump])
        self._csv_file.flush()

    def _close_csv(self) -> None:
        """Close the CSV file handle (best-effort)."""
        if self._csv_file is not None:
            try:
                self._csv_file.close()
            except Exception:
                pass
            self._csv_file = None
            self._csv_writer = None
