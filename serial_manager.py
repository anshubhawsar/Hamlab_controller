"""
serial_manager.py
=================

ESP32 serial communication manager for the HAM Lab cooling system.

This module provides a thread-safe, production-ready wrapper around pySerial
that:

* Automatically detects the ESP32 COM port when the device is plugged in
    (USB-serial VID/PID matching with a boot-banner / TEMP probe fallback).
* Opens/closes the serial connection at 115200 baud.
* Runs a background daemon reader thread that parses ``TEMP,..`` frames
  without ever blocking the Tkinter UI thread.
* Sends commands (``ON`` / ``OFF`` / ``AUTO`` / ``STATUS``) terminated with
    a newline.
* Automatically reconnects (every few seconds) when the device is unplugged
  and plugged back in.
* Handles every serial exception gracefully through observer callbacks.

The class intentionally knows nothing about the GUI: it only exposes
callbacks (``on_data``, ``on_connection_change``) and a small thread-safe
state API so it can be embedded in any UI framework.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

# pySerial is an optional hard dependency: the application should still boot
# (and show a friendly message) when it is not installed.
try:
    import serial
    from serial.tools import list_ports

    _SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    serial = None  # type: ignore
    list_ports = None  # type: ignore
    _SERIAL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BAUDRATE = 115200  # ESP32 firmware baud rate
TIMEOUT_READ = 0.5  # seconds, read timeout for the background thread
TIMEOUT_WRITE = 1.0  # seconds, write timeout for commands
RECONNECT_INTERVAL_SECONDS = 3.0  # how often we retry after a disconnect
PROBE_WAIT_SECONDS = 0.4  # short read slice while waiting for boot output

# Data frame prefix emitted by the ESP32 firmware.
FRAME_PREFIX = "TEMP"

# Known USB serial VID/PID pairs used by common ESP32 dev boards.
# These are used only as a *hint*; the STATUS probe is the authoritative test.
KNOWN_ESP32_VID_PID = {
    (0x10C4, 0xEA60),  # Silicon Labs CP210x
    (0x1A86, 0x7523),  # WCH CH340 / CH341
    (0x303A, 0x1001),  # Espressif native USB (ESP32-S3 etc.)
    (0x1A86, 0x55D4),  # CH9102
    (0x2341, 0x0043),  # Arduino-compatible boards (generic)
}

#: Parsed payload returned to observers. Kept as a dict for readability.
ParsedFrame = Dict[str, object]

# Telemetry block markers used by the multi-line STATUS/telemetry format the
# device firmware emits (e.g. "Inlet Temp", "Outlet Temp", "Flow Rate",
# "PWM", "Pump").
_BLOCK_INLET_KEY = "inlet temp"
_BLOCK_OUTLET_KEY = "outlet temp"
_BLOCK_FLOW_KEY = "flow rate"
_BLOCK_PWM_KEY = "pwm"
_BLOCK_PUMP_KEY = "pump"


# ---------------------------------------------------------------------------
# Helper: safe attribute access (avoids crash when pySerial is absent)
# ---------------------------------------------------------------------------
def _serial_attr(module, name: str, default=None):
    """Return ``getattr(module, name, default)`` only when module is not None.

    This keeps the rest of the code clean when pySerial is missing while still
    satisfying type checkers.
    """
    if module is None:
        return default
    return getattr(module, name, default)


# ---------------------------------------------------------------------------
# Public helper: parse a raw ESP32 line into a dictionary.
# ---------------------------------------------------------------------------
def parse_serial_line(line: str) -> Optional[ParsedFrame]:
    """Parse one line received from the ESP32.

    Two formats are supported for backward compatibility.

    New key/value format (L298N + YF-S201 firmware)::

        TEMP,<inlet>,<outlet>,FLOW,<flowRate>,PWM,<pwmValue>,PUMP,<ON/OFF>

    Example::

        TEMP,27.4,36.8,FLOW,3.12,PWM,182,PUMP,ON

    Legacy CSV format::

        TEMP,<inlet>,<outlet>,<pump_status>

    Example::

        TEMP,32.45,30.18,ON

    Args:
        line: The raw, stripped line from the serial port.

    Returns:
        A dict with keys ``inlet_temp``, ``outlet_temp``, ``pump`` and (when
        present in the frame) ``flow_rate`` and ``pwm``. Returns ``None`` when
        the line is not a valid ``TEMP`` frame.

    Raises:
        ValueError: When the frame has the right prefix but malformed fields.
    """
    if not line or not line.startswith(FRAME_PREFIX):
        return None

    parts = line.split(",")
    if not parts or parts[0].strip().upper() != FRAME_PREFIX:
        raise ValueError(f"Malformed TEMP frame: {line!r}")

    # Fast path: the new key/value format contains FLOW / PWM / PUMP tokens.
    tokens = [p.strip().upper() for p in parts]

    if "FLOW" in tokens or "PWM" in tokens or "PUMP" in tokens:
        return _parse_kv_frame(parts, line)

    # Legacy CSV format: TEMP,<inlet>,<outlet>,<pump>
    if len(parts) != 4:
        raise ValueError(f"Malformed TEMP frame: {line!r}")

    inlet_temp = float(parts[1].strip())
    outlet_temp = float(parts[2].strip())
    pump_status = parts[3].strip().upper()

    if pump_status not in ("ON", "OFF"):
        raise ValueError(f"Unknown pump status in frame: {line!r}")

    return {
        "inlet_temp": inlet_temp,
        "outlet_temp": outlet_temp,
        "pump": pump_status,
    }


def _parse_kv_frame(parts, line: str) -> ParsedFrame:
    """Parse the L298N key/value frame.

    Two shapes are accepted for backward compatibility.

    Full (two temperatures):: 

        inlet,outlet,FLOW,<flow>,PWM,<pwm>,PUMP,<ON/OFF>

    Compact (single temperature):: 

        inlet,FLOW,<flow>,PWM,<pwm>,PUMP,<ON/OFF>

    The remaining tokens are ``KEY,value`` pairs.
    """
    # At minimum we need TEMP + one temperature value.
    if len(parts) < 3:
        raise ValueError(f"Malformed TEMP frame: {line!r}")
    inlet_temp = float(parts[1].strip())

    # The next token is the outlet temperature when it is numeric; some
    # firmware builds only emit a single temperature, in which case we
    # default the outlet to 0.0 and start the key/value walk right away.
    outlet_temp = 0.0
    start_index = 2
    try:
        outlet_temp = float(parts[2].strip())
        start_index = 3
    except ValueError:
        pass

    frame: ParsedFrame = {
        "inlet_temp": inlet_temp,
        "outlet_temp": outlet_temp,
        # Defaults for the new fields (present in the new firmware).
        "flow_rate": 0.0,
        "pwm": 0,
        "pump": "OFF",
    }

    # Walk the remaining tokens as KEY,value pairs.
    i = start_index
    while i < len(parts):
        key = parts[i].strip().upper()
        if i + 1 >= len(parts):
            break
        value = parts[i + 1].strip()
        if key == "FLOW":
            try:
                frame["flow_rate"] = float(value)
            except ValueError:
                pass
        elif key == "PWM":
            try:
                frame["pwm"] = int(float(value))
            except ValueError:
                pass
        elif key == "PUMP":
            up = value.upper()
            if up in ("ON", "OFF"):
                frame["pump"] = up
        i += 2

    return frame


# ---------------------------------------------------------------------------
# SerialManager
# ---------------------------------------------------------------------------
class SerialManager:
    """Thread-safe manager for an ESP32 serial connection.

    Responsibilities:

    * Detect/refresh available ESP32 COM ports.
    * Open/close the serial connection on a background-agnostic way.
    * Run one daemon reader thread that keeps parsing incoming frames.
    * Send commands with a trailing newline, protected by a write lock.
    * Auto-reconnect loop with a fixed interval after unexpected drops.
    * Notify observers without raising any exception into the GUI.

    Threading model
    ---------------
    * ``_reader_loop`` is the only thread that reads from the port.
    * ``send_command`` may be called from any thread; a lock guards writes.
    * ``_reconnect_loop`` is the only thread that opens/creates connections,
      so the reader thread is always started for a connection it owns.

    Public attributes
    -----------------
    on_data: Optional[Callable[[ParsedFrame], None]]
        Invoked (from the reader thread) for every valid ``TEMP`` frame.
    on_connection_change: Optional[Callable[[str, Optional[str]], None]]
        Invoked whenever the connection state flips. Receives
        ``("connected", port)`` or ``("disconnected", None)``.
    """

    def __init__(
        self,
        on_data: Optional[Callable[[ParsedFrame], None]] = None,
        on_connection_change: Optional[Callable[[str, Optional[str]], None]] = None,
    ) -> None:
        """Create a SerialManager.

        Args:
            on_data: Observer called for each parsed TEMP frame.
on_connection_change: Observer called on connection state changes.
        """
        self.on_data = on_data
        self.on_connection_change = on_connection_change

        # --- state (protected by _lock) ---
        self._lock = threading.RLock()
        self._port: Optional[str] = None
        self._requested_port: Optional[str] = None  # last port we *wanted* to use
        self._ser: Optional[object] = None  # serial.Serial instance or None
        self._connected = False

        # --- threads ---
        self._reader_thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()

        # --- buffering for partial lines ---
        self._line_buffer = ""

        # --- block-format accumulation state ---
        self._block_values: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    @staticmethod
    def is_available() -> bool:
        """Return True when pySerial is importable in this environment."""
        return _SERIAL_AVAILABLE

    # ------------------------------------------------------------------
    # Port discovery
    # ------------------------------------------------------------------
    @staticmethod
    def list_serial_ports() -> List[str]:
        """Return every COM port currently reported by the OS.

        Returns:
            A list of port names (e.g. ``["COM3", "COM7"]``). Empty when
            pySerial is not installed.
        """
        if not _SERIAL_AVAILABLE:
            return []
        return [p.device for p in list_ports.comports()]

    @staticmethod
    def _port_looks_like_esp32(port_info) -> bool:
        """Best-effort check using USB VID/PID when the OS exposes them.

        ``port_info`` is a ``serial.tools.list_ports_common.ListPortInfo``.
        Returns False when no hardware info is available (we then rely on the
        boot banner or periodic TEMP frames to make the final decision).
        """
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        if vid is None or pid is None:
            return False
        return (int(vid) & 0xFFFF, int(pid) & 0xFFFF) in KNOWN_ESP32_VID_PID

    def detect_esp32_port(self, preferred: Optional[str] = None) -> Optional[str]:
        """Detect the most likely ESP32 COM port.

        Strategy:
            1. If ``preferred`` is given and openable, return it.
            2. Check known ESP32 VID/PID pairs first.
            3. Fall back to probing every other port for the ESP32 boot banner,
               a valid ``TEMP`` frame, or the telemetry block format.

        Args:
            preferred: An optional port the user selected previously.

        Returns:
            The detected port name or ``None`` when no ESP32 was found.
        """
        if not _SERIAL_AVAILABLE:
            return None

        # 1) Preferred port fast-path
        if preferred:
            try:
                if self._probe_port(preferred):
                    return preferred
            except Exception:
                # Probe failed -> continue with discovery
                pass

        ports = list(list_ports.comports())
        # 2) VID/PID hint
        for info in ports:
            if self._port_looks_like_esp32(info):
                try:
                    if self._probe_port(info.device):
                        return info.device
                except Exception:
                    continue

        # 3) Generic probe fallback (covers unknown drivers / adapters)
        for info in ports:
            device = info.device
            if device == preferred:
                continue  # already probed above
            try:
                if self._probe_port(device):
                    return device
            except Exception:
                continue

        return None

    def _probe_port(self, port: str) -> bool:
        """Probe a port to check if it is the ESP32 cooling controller.

        The ESP32 resets when DTR toggles (pySerial default on open).  We
        immediately lower DTR/RTS so the device boots into its application and
        starts streaming.  This method then looks for:
        1. The startup banner ``ESP32 Cooling System Started``.
        2. Periodic ``TEMP,...`` frames (sent every 1 s).
        3. The multi-line telemetry block (``Inlet Temp`` / ``Outlet Temp`` /
           ``Pump``).

        Args:
            port: The COM port name to probe.

        Returns:
            True when the port appears to be the ESP32 cooling controller.
        """
        if not _SERIAL_AVAILABLE:
            return False
        probe_ser = None
        try:
            serial_cls = _serial_attr(serial, "Serial")
            if serial_cls is None:
                return False
            probe_ser = serial_cls(
                port=port,
                baudrate=BAUDRATE,
                timeout=0.5,
                write_timeout=TIMEOUT_WRITE,
            )
            # Disable DTR/RTS so the ESP32 does not get caught in bootloader
            # mode on Windows (pySerial asserts these by default on open).
            try:
                probe_ser.dtr = False
                probe_ser.rts = False
            except Exception:
                pass

            # Phase 1: wait for the ESP32 to boot and start streaming.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                raw = probe_ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line == "ESP32 Cooling System Started":
                    return self._wait_for_esp32_data(probe_ser, timeout=3.0)
                if line.startswith(FRAME_PREFIX):
                    return True
                if line.startswith("ERROR"):
                    return self._wait_for_esp32_data(probe_ser, timeout=2.5)
                if self._line_is_block_member(line):
                    return self._wait_for_esp32_data(probe_ser, timeout=3.0)

            # Phase 2: if we missed the boot banner, keep waiting briefly for
            # the normal periodic TEMP stream / telemetry block.
            return self._wait_for_esp32_data(probe_ser, timeout=3.0)

        except Exception:
            return False
        finally:
            if probe_ser is not None:
                try:
                    probe_ser.close()
                except Exception:
                    pass

    @staticmethod
    def _line_is_block_member(line: str) -> bool:
        """Return True when a line looks like part of the telemetry block."""
        lowered = line.lower()
        return (
            _BLOCK_INLET_KEY in lowered
            or _BLOCK_OUTLET_KEY in lowered
            or _BLOCK_PUMP_KEY in lowered
            or "flow rate" in lowered
            or "pwm" in lowered
        )

    def _wait_for_esp32_data(self, ser, timeout: float = 2.0) -> bool:
        """Read from an open serial port and wait for ESP32 data.

        Accepts a ``TEMP,...`` frame, a telemetry block line, or the boot
        banner as evidence the ESP32 cooling controller is present.

        Args:
            ser: An open ``serial.Serial`` instance.
            timeout: Maximum seconds to wait.

        Returns:
            True when ESP32 data was received.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = ser.readline()
            except Exception:
                return False
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if line.startswith(FRAME_PREFIX) or self._line_is_block_member(line):
                return True
        return False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect(self, port: Optional[str] = None) -> bool:
        """Open the serial connection to the ESP32.

        When ``port`` is None the port is auto-detected. This method is safe
        to call from any thread. It returns True on success. When the device
        is not present it returns False *without* raising; the auto-reconnect
        loop takes care of retrying.

        Args:
            port: Optional explicit port, e.g. ``"COM5"``.

        Returns:
            True when the connection was established.
        """
        if not _SERIAL_AVAILABLE:
            self._notify_connection("disconnected", None)
            return False

        # If we are already connected to this port, do nothing.
        with self._lock:
            if self._connected:
                if not port or port == self._port:
                    return True
                # Switching ports -> close the old one first.
                self._close_serial()

        target = port or self.detect_esp32_port()
        if not target:
            self._notify_connection("disconnected", None)
            return False

        serial_cls = _serial_attr(serial, "Serial")
        if serial_cls is None:
            return False

        # Open without dtr/rts in the constructor: some pySerial builds raise
        # on these kwargs. We lower DTR/RTS right after opening instead.
        try:
            ser = serial_cls(
                port=target,
                baudrate=BAUDRATE,
                timeout=TIMEOUT_READ,
                write_timeout=TIMEOUT_WRITE,
            )
        except Exception as exc:
            # Graceful: the reconnect loop will retry.
            self._safe_log(f"Connect failed on {target}: {exc}")
            self._notify_connection("disconnected", None)
            return False

        # Disable DTR/RTS so the ESP32 does not get caught in bootloader mode.
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass

        with self._lock:
            self._ser = ser
            self._port = target
            self._connected = True

        self._line_buffer = ""
        self._block_values = {}
        self._stop_event.clear()

        # Start the reader thread for this connection.
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="ESP32SerialReader",
            daemon=True,
        )
        self._reader_thread.start()

        self._notify_connection("connected", target)
        return True

    def disconnect(self) -> None:
        """Close the serial connection and stop all background threads.

        Safe to call multiple times and from any thread.
        """
        self._stop_event.set()
        with self._lock:
            self._close_serial()
        self._notify_connection("disconnected", None)

    def _close_serial(self) -> None:
        """Internal: close the underlying serial object (caller holds lock)."""
        ser = self._ser
        self._ser = None
        self._connected = False
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._port = None

    # ------------------------------------------------------------------
    # Command sending
    # ------------------------------------------------------------------
    def send_command(self, command: str) -> bool:
        """Send a command to the ESP32.

        Every command is terminated with ``\\n`` as required by the firmware.

        Args:
            command: One of ``ON``, ``OFF``, ``AUTO`` or ``STATUS``.

        Returns:
            True when the command was written to the port successfully.
        """
        if not _SERIAL_AVAILABLE:
            return False
        with self._lock:
            ser = self._ser
            if ser is None or not self._connected:
                return False

        payload = f"{command.strip()}\n".encode("utf-8")
        try:
            with self._write_lock:
                ser.write(payload)
            return True
        except Exception:
            # A failed write usually means the device vanished -> trigger a
            # reconnect attempt from the reader thread (it will hit an
            # exception and stop). Force the state update immediately too.
            self._on_connection_lost()
            return False

    # ------------------------------------------------------------------
    # State accessors (thread-safe)
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        """Return True when a serial connection is currently open."""
        with self._lock:
            return self._connected

    def get_port(self) -> Optional[str]:
        """Return the currently connected port name (or None)."""
        with self._lock:
            return self._port

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------
    def start_auto_reconnect(self) -> None:
        """Start the auto-reconnect worker.

        The worker wakes every ``RECONNECT_INTERVAL_SECONDS`` and tries to
        connect whenever the device is currently disconnected. It stops when
        ``shutdown()`` is called.
        """
        if not _SERIAL_AVAILABLE:
            return
        if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
            return
        self._stop_event.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            name="ESP32AutoReconnect",
            daemon=True,
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Background worker that keeps trying to reconnect while stopped=False."""
        while not self._stop_event.is_set():
            if not self.is_connected():
                # Try to (re)connect; ignore failures silently here.
                try:
                    self.connect(self._last_requested_port())
                except Exception:
                    pass
            # Sleep in small slices so shutdown() is responsive.
            deadline = time.monotonic() + RECONNECT_INTERVAL_SECONDS
            while time.monotonic() < deadline and not self._stop_event.is_set():
                time.sleep(0.1)

    def _last_requested_port(self) -> Optional[str]:
        """Return the port we last tried to use (for reconnect targeting)."""
        return self._port

    def _reader_loop(self) -> None:
        """Daemon thread: continuously read and parse lines from the ESP32."""
        while not self._stop_event.is_set():
            ser = None
            with self._lock:
                if self._connected:
                    ser = self._ser
            if ser is None:
                time.sleep(0.2)
                continue

            try:
                raw = ser.readline()
            except Exception:
                # The device disappeared while reading.
                self._on_connection_lost()
                return

            if not raw:
                # readline() timeout with no data -> loop again.
                continue

            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                continue

            frame = self._consume_line(line)
            if frame is not None:
                self._dispatch_data(frame)

    def _consume_line(self, chunk: str) -> Optional[ParsedFrame]:
        """Feed raw text into an internal buffer and return complete frames.

        Serial data may arrive split across ``readline()`` calls, so we keep
        a running buffer and only treat newline-terminated content as frames.

        Supports both the single-line ``TEMP,<inlet>,<outlet>,<pump>`` format
        and the multi-line telemetry block format (``Inlet Temp`` /
        ``Outlet Temp`` / ``Pump``).
        """
        self._line_buffer += chunk
        if "\n" not in self._line_buffer:
            return None

        # Process each complete line in order.
        result = None
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue

            # 1) Single-line TEMP, CSV format.
            try:
                parsed = parse_serial_line(line)
            except Exception:
                # Malformed frame -> ignore, do not crash the reader.
                parsed = None
            if parsed is not None:
                result = parsed
                continue

            # 2) Multi-line telemetry block format.
            block_frame = self._consume_block_line(line)
            if block_frame is not None:
                result = block_frame
        return result

    def _consume_block_line(self, line: str) -> Optional[ParsedFrame]:
        """Accumulate telemetry block lines and return a frame when complete.

        The device may emit a block like::

            Inlet Temp  : 28.56 C
            Outlet Temp : 28.25 C
            Flow Rate   : 0.00 L/min
            PWM         : 80
            Pump        : ON

        We remember the last seen ``Inlet Temp``, ``Outlet Temp`` and ``Pump``
        values and produce a frame once we have all three. A separator line
        (``---...``) resets the accumulator so each block yields one frame.

        Args:
            line: A single stripped line from the serial port.

        Returns:
            A ``ParsedFrame`` dict or ``None``.
        """
        lowered = line.lower()

        # Separator line -> reset accumulation.
        if line.startswith("-"):
            self._block_values = {}
            return None

        # "Inlet Temp : 28.56 C"
        if _BLOCK_INLET_KEY in lowered:
            value = self._parse_block_number(line)
            if value is not None:
                self._block_values[_BLOCK_INLET_KEY] = value
        # "Outlet Temp : 28.25 C"
        elif _BLOCK_OUTLET_KEY in lowered:
            value = self._parse_block_number(line)
            if value is not None:
                self._block_values[_BLOCK_OUTLET_KEY] = value
        # "Flow Rate : 3.12 L/min"
        elif _BLOCK_FLOW_KEY in lowered:
            value = self._parse_block_number(line)
            if value is not None:
                self._block_values[_BLOCK_FLOW_KEY] = value
        # "PWM : 182"
        elif _BLOCK_PWM_KEY in lowered:
            value = self._parse_block_number(line)
            if value is not None:
                self._block_values[_BLOCK_PWM_KEY] = value
        # "Pump : ON"
        elif _BLOCK_PUMP_KEY in lowered:
            pump = self._extract_pump(line)
            if pump is not None:
                self._block_values[_BLOCK_PUMP_KEY] = pump

        # Only emit a frame when we have all three required fields.
        if (
            _BLOCK_INLET_KEY in self._block_values
            and _BLOCK_OUTLET_KEY in self._block_values
            and _BLOCK_PUMP_KEY in self._block_values
        ):
            try:
                inlet = float(self._block_values[_BLOCK_INLET_KEY])
                outlet = float(self._block_values[_BLOCK_OUTLET_KEY])
            except (TypeError, ValueError):
                return None
            pump = str(self._block_values[_BLOCK_PUMP_KEY]).upper()
            # Optional fields present in the reference firmware block format.
            flow_rate = 0.0
            pwm = 0
            flow_val = self._block_values.get(_BLOCK_FLOW_KEY)
            pwm_val = self._block_values.get(_BLOCK_PWM_KEY)
            if flow_val is not None:
                try:
                    flow_rate = float(flow_val)
                except (TypeError, ValueError):
                    pass
            if pwm_val is not None:
                try:
                    pwm = int(float(pwm_val))
                except (TypeError, ValueError):
                    pass
            # Reset so the next block starts fresh.
            self._block_values = {}
            return {
                "inlet_temp": inlet,
                "outlet_temp": outlet,
                "pump": pump,
                "flow_rate": flow_rate,
                "pwm": pwm,
            }

        return None

    @staticmethod
    def _parse_block_number(line: str) -> Optional[str]:
        """Extract the first floating-point token from a block line.

        E.g. ``Inlet Temp  : 28.56 C`` -> ``"28.56"``. Returns None when no
        number is found.
        """
        tokens = line.split()
        for tok in tokens:
            candidate = tok.strip()
            if candidate == ":" or candidate == "C" or candidate == "L/min":
                continue
            try:
                float(candidate)
                return candidate
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_pump(line: str) -> Optional[str]:
        """Extract the pump state (ON/OFF) from a block line."""
        lowered = line.lower()
        if "on" in lowered:
            return "ON"
        if "off" in lowered:
            return "OFF"
        return None

    def _dispatch_data(self, frame: ParsedFrame) -> None:
        """Invoke the on_data observer (best-effort, never raises)."""
        if self.on_data is None:
            return
        try:
            self.on_data(frame)
        except Exception as exc:
            self._safe_log(f"on_data observer error: {exc}")

    def _on_connection_lost(self) -> None:
        """Handle an unexpected drop: update state and notify observers."""
        changed = False
        with self._lock:
            if self._connected:
                self._close_serial()
                changed = True
        if changed:
            self._notify_connection("disconnected", None)
            self._safe_log("ESP32 disconnected unexpectedly; auto-reconnect active.")

    def _notify_connection(self, state: str, port: Optional[str]) -> None:
        """Best-effort observer callback for connection state changes."""
        if self.on_connection_change is None:
            return
        try:
            self.on_connection_change(state, port)
        except Exception as exc:
            self._safe_log(f"on_connection_change observer error: {exc}")

    @staticmethod
    def _safe_log(message: str) -> None:
        """Print a diagnostic message without ever raising."""
        try:
            print(f"[SerialManager] {message}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop all threads and close the port.

        Call this once when the application is about to exit.
        """
        self._stop_event.set()
        with self._lock:
            self._close_serial()
        # Give threads a moment to observe the stop event.
        time.sleep(0.05)
