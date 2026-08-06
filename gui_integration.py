"""
gui_integration.py
==================

Tkinter/CustomTkinter UI panel that integrates the ESP32 cooling system into
the existing HAM Lab application without modifying the rest of the UI.

The module defines :class:`CoolingPanel`, a ``ctk.CTkFrame`` that:

* Shows the connection status with a green/red indicator.
* Displays the active COM port and a dropdown to pick a port.
* Shows live inlet/outlet temperatures, flow rate, pump PWM and mode.
* Provides Connect / Disconnect / Pump ON / Pump OFF / AUTO Mode buttons and
  a manual PWM speed slider (active only in MANUAL mode).
* Keeps the GUI responsive: every serial callback is marshalled back onto the
  Tkinter main thread with ``widget.after(0, ...)``.

The panel receives a plain ``colors`` dict from the host application so it
reuses the existing light/dark theme palette without importing the main app
module (which avoids circular-import problems).
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None

import customtkinter as ctk

# Local modules (created as part of the ESP32 integration).
from cooling_controller import CoolingController
from serial_manager import SerialManager


class CoolingPanel(ctk.CTkScrollableFrame):
    """A themed dashboard panel for monitoring/controlling the ESP32 pump.

    The panel is scrollable so every card (connection, telemetry, controls)
    stays visible even on small windows.

    Args:
        master: Parent widget (usually the main container of the app).
        colors: Dictionary of theme color constants used by the host app
            (``bg_secondary``, ``text_primary``, ``accent_blue``, ...).
        on_log: Optional callback ``(message)`` for status messages.
    """

    def __init__(
        self,
        master,
        colors: Optional[Dict[str, str]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent", orientation="vertical")
        self._colors = colors or {}
        self._on_log = on_log

        # ------------------------------------------------------------------
        # Controller (shared with the rest of the app via the same instance)
        # ------------------------------------------------------------------
        self.controller = CoolingController(
            on_data=self._on_temp_data,
            on_connection_change=self._on_connection_change,
        )

        # --- internal UI state ---
        self._connected = False
        self._last_inlet: Optional[float] = None
        self._last_outlet: Optional[float] = None
        self._pump: str = "OFF"
        self._mode: str = "MANUAL"
        self._port: Optional[str] = None
        self._flow_rate: float = 0.0
        self._pwm: int = 0
        self._buzzer_latched = False

        # ------------------------------------------------------------------
        # Layout
        # ------------------------------------------------------------------
        self._build_ui()

        # Populate the port dropdown in the background.
        threading.Thread(target=self._refresh_ports_worker, daemon=True).start()

    # ==================================================================
    # UI construction
    # ==================================================================
    def _c(self, key: str, fallback: str = "") -> str:
        """Return a theme color by key (fallback to an empty string)."""
        return self._colors.get(key, fallback)

    def _build_ui(self) -> None:
        """Create every widget of the cooling dashboard."""
        # Title card
        title_card = ctk.CTkFrame(
            self,
            fg_color=self._c("bg_secondary", "#ffffff"),
            corner_radius=12,
            border_width=2,
            border_color=self._c("border_light", "#d1d8e0"),
        )
        title_card.pack(fill="x", padx=18, pady=(18, 10))

        ctk.CTkLabel(
            title_card,
            text="❄️ Cooling System (ESP32)",
            font=("Arial", 20, "bold"),
            text_color=self._c("text_primary", "#0d1b2a"),
        ).pack(anchor="w", padx=20, pady=(14, 4))

        ctk.CTkLabel(
            title_card,
            text="Monitor inlet/outlet temperatures and control the pump over USB Serial.",
            font=("Arial", 13),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(anchor="w", padx=20, pady=(0, 14))

        # ------------------------------------------------------------------
        # Connection card
        # ------------------------------------------------------------------
        conn_card = ctk.CTkFrame(
            self,
            fg_color=self._c("bg_secondary", "#ffffff"),
            corner_radius=12,
            border_width=2,
            border_color=self._c("border_light", "#d1d8e0"),
        )
        conn_card.pack(fill="x", padx=18, pady=10)

        self._build_connection_card(conn_card)

        # ------------------------------------------------------------------
        # Status / telemetry card
        # ------------------------------------------------------------------
        tele_card = ctk.CTkFrame(
            self,
            fg_color=self._c("bg_secondary", "#ffffff"),
            corner_radius=12,
            border_width=2,
            border_color=self._c("border_light", "#d1d8e0"),
        )
        tele_card.pack(fill="x", padx=18, pady=10)

        self._build_telemetry_card(tele_card)

        # ------------------------------------------------------------------
        # Control buttons card
        # ------------------------------------------------------------------
        ctrl_card = ctk.CTkFrame(
            self,
            fg_color=self._c("bg_secondary", "#ffffff"),
            corner_radius=12,
            border_width=2,
            border_color=self._c("border_light", "#d1d8e0"),
        )
        ctrl_card.pack(fill="x", padx=18, pady=10)

        self._build_controls_card(ctrl_card)

    def _build_connection_card(self, parent) -> None:
        """Build the top connection card (indicator, port dropdown, connect)."""
        # --- Connection status indicator ---
        status_frame = ctk.CTkFrame(parent, fg_color="transparent")
        status_frame.pack(fill="x", padx=20, pady=(16, 6))

        self.lbl_status = ctk.CTkLabel(
            status_frame,
            text="● Disconnected",
            font=("Arial", 15, "bold"),
            text_color=self._c("accent_red", "#ae2a19"),
        )
        self.lbl_status.pack(side="left")

        self.lbl_port = ctk.CTkLabel(
            status_frame,
            text="Port: --",
            font=("Arial", 13, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        )
        self.lbl_port.pack(side="right")

        # --- Port selection + buttons ---
        port_frame = ctk.CTkFrame(parent, fg_color="transparent")
        port_frame.pack(fill="x", padx=20, pady=(10, 16))

        self.opt_port = ctk.CTkOptionMenu(
            port_frame,
            values=["Auto-detect"],
            fg_color=self._c("bg_tertiary", "#f0f3f8"),
            button_color=self._c("accent_blue", "#0052cc"),
            button_hover_color=self._c("accent_blue", "#0052cc"),
            text_color=self._c("input_text", "#0d1b2a"),
            dropdown_fg_color=self._c("bg_secondary", "#ffffff"),
            dropdown_hover_color=self._c("accent_blue_light", "#e3f0ff"),
            dropdown_text_color=self._c("input_text", "#0d1b2a"),
            width=160,
        )
        self.opt_port.pack(side="left", padx=(0, 8))

        self.btn_connect = ctk.CTkButton(
            port_frame,
            text="Connect",
            width=110,
            fg_color=self._c("accent_green", "#216e4e"),
            hover_color=self._c("accent_green", "#216e4e"),
            command=self._on_connect,
            font=("Arial", 12, "bold"),
        )
        self.btn_connect.pack(side="left", padx=4)

        self.btn_disconnect = ctk.CTkButton(
            port_frame,
            text="Disconnect",
            width=110,
            fg_color=self._c("accent_red", "#ae2a19"),
            hover_color=self._c("accent_red", "#ae2a19"),
            command=self._on_disconnect,
            state="disabled",
            font=("Arial", 12, "bold"),
        )
        self.btn_disconnect.pack(side="left", padx=4)

        self.btn_refresh = ctk.CTkButton(
            port_frame,
            text="Refresh Ports",
            width=120,
            fg_color=self._c("bg_tertiary", "#f0f3f8"),
            hover_color=self._c("bg_sidebar", "#e8ecf1"),
            text_color=self._c("text_primary", "#0d1b2a"),
            command=self._on_refresh_ports,
            font=("Arial", 12, "bold"),
        )
        self.btn_refresh.pack(side="left", padx=4)

    def _build_telemetry_card(self, parent) -> None:
        """Build the telemetry card (two temperature readouts + state)."""
        metrics = ctk.CTkFrame(parent, fg_color="transparent")
        metrics.pack(fill="x", padx=20, pady=(16, 6))

        # Inlet temperature
        inlet_frame = ctk.CTkFrame(metrics, fg_color="transparent")
        inlet_frame.grid(row=0, column=0, sticky="nsew", padx=8)
        ctk.CTkLabel(
            inlet_frame,
            text="INLET TEMPERATURE",
            font=("Arial", 12, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack()
        self.lbl_inlet = ctk.CTkLabel(
            inlet_frame,
            text="-- °C",
            font=("Arial", 30, "bold"),
            text_color=self._c("accent_blue", "#0052cc"),
        )
        self.lbl_inlet.pack(pady=(4, 10))

        # Outlet temperature
        outlet_frame = ctk.CTkFrame(metrics, fg_color="transparent")
        outlet_frame.grid(row=0, column=1, sticky="nsew", padx=8)
        ctk.CTkLabel(
            outlet_frame,
            text="OUTLET TEMPERATURE",
            font=("Arial", 12, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack()
        self.lbl_outlet = ctk.CTkLabel(
            outlet_frame,
            text="-- °C",
            font=("Arial", 30, "bold"),
            text_color=self._c("accent_orange", "#974f0c"),
        )
        self.lbl_outlet.pack(pady=(4, 10))

        # Pump + Mode
        state_frame = ctk.CTkFrame(metrics, fg_color="transparent")
        state_frame.grid(row=0, column=2, sticky="nsew", padx=8)

        pump_row = ctk.CTkFrame(state_frame, fg_color="transparent")
        pump_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            pump_row,
            text="PUMP: ",
            font=("Arial", 13, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(side="left")
        self.lbl_pump = ctk.CTkLabel(
            pump_row,
            text="OFF",
            font=("Arial", 16, "bold"),
            text_color=self._c("accent_red", "#ae2a19"),
        )
        self.lbl_pump.pack(side="left")

        mode_row = ctk.CTkFrame(state_frame, fg_color="transparent")
        mode_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            mode_row,
            text="MODE:  ",
            font=("Arial", 13, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(side="left")
        self.lbl_mode = ctk.CTkLabel(
            mode_row,
            text="MANUAL",
            font=("Arial", 16, "bold"),
            text_color=self._c("accent_orange", "#974f0c"),
        )
        self.lbl_mode.pack(side="left")

        flow_row = ctk.CTkFrame(state_frame, fg_color="transparent")
        flow_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            flow_row,
            text="FLOW: ",
            font=("Arial", 13, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(side="left")
        self.lbl_flow = ctk.CTkLabel(
            flow_row,
            text="0.0 L/min",
            font=("Arial", 16, "bold"),
            text_color=self._c("accent_blue", "#0052cc"),
        )
        self.lbl_flow.pack(side="left")

        pwm_row = ctk.CTkFrame(state_frame, fg_color="transparent")
        pwm_row.pack(fill="x", pady=6)
        ctk.CTkLabel(
            pwm_row,
            text="PWM:  ",
            font=("Arial", 13, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(side="left")
        self.lbl_pwm = ctk.CTkLabel(
            pwm_row,
            text="0 %",
            font=("Arial", 16, "bold"),
            text_color=self._c("accent_orange", "#974f0c"),
        )
        self.lbl_pwm.pack(side="left")

        metrics.columnconfigure(0, weight=1)
        metrics.columnconfigure(1, weight=1)
        metrics.columnconfigure(2, weight=1)

        self.lbl_msg = ctk.CTkLabel(
            parent,
            text="",
            font=("Arial", 12, "italic"),
            text_color=self._c("text_dim", "#6b7684"),
        )
        self.lbl_msg.pack(fill="x", padx=20, pady=(4, 14))

    def _build_controls_card(self, parent) -> None:
        """Build the control buttons card (Pump ON/OFF, AUTO mode, PWM slider)."""
        ctk.CTkLabel(
            parent,
            text="PUMP CONTROL",
            font=("Arial", 14, "bold"),
            text_color=self._c("text_primary", "#0d1b2a"),
        ).pack(anchor="w", padx=20, pady=(14, 6))

        btns = ctk.CTkFrame(parent, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 10))

        self.btn_pump_on = ctk.CTkButton(
            btns,
            text="💧 Pump ON",
            width=140,
            height=44,
            fg_color=self._c("accent_green", "#216e4e"),
            hover_color=self._c("accent_green", "#216e4e"),
            command=self._on_pump_on,
            state="disabled",
            font=("Arial", 14, "bold"),
        )
        self.btn_pump_on.pack(side="left", padx=6)

        self.btn_pump_off = ctk.CTkButton(
            btns,
            text="💧 Pump OFF",
            width=140,
            height=44,
            fg_color=self._c("accent_red", "#ae2a19"),
            hover_color=self._c("accent_red", "#ae2a19"),
            command=self._on_pump_off,
            state="disabled",
            font=("Arial", 14, "bold"),
        )
        self.btn_pump_off.pack(side="left", padx=6)

        self.btn_auto = ctk.CTkButton(
            btns,
            text="⚙️ AUTO Mode",
            width=150,
            height=44,
            fg_color=self._c("accent_blue", "#0052cc"),
            hover_color=self._c("accent_blue", "#0052cc"),
            command=self._on_auto_mode,
            state="disabled",
            font=("Arial", 14, "bold"),
        )
        self.btn_auto.pack(side="left", padx=6)

        # --- Manual PWM speed control (only active in MANUAL mode) ---
        pwm_frame = ctk.CTkFrame(parent, fg_color="transparent")
        pwm_frame.pack(fill="x", padx=20, pady=(0, 16))

        pwm_label_frame = ctk.CTkFrame(pwm_frame, fg_color="transparent")
        pwm_label_frame.pack(fill="x")
        ctk.CTkLabel(
            pwm_label_frame,
            text="MANUAL PUMP SPEED (PWM)",
            font=("Arial", 12, "bold"),
            text_color=self._c("text_secondary", "#3d4756"),
        ).pack(side="left")
        self.lbl_pwm_set = ctk.CTkLabel(
            pwm_label_frame,
            text="0",
            font=("Arial", 14, "bold"),
            text_color=self._c("accent_blue", "#0052cc"),
        )
        self.lbl_pwm_set.pack(side="right")

        self.slider_pwm = ctk.CTkSlider(
            pwm_frame,
            from_=0,
            to=255,
            number_of_steps=255,
            command=self._on_pwm_slide,
            fg_color=self._c("bg_tertiary", "#f0f3f8"),
            button_color=self._c("accent_blue", "#0052cc"),
            progress_color=self._c("accent_blue", "#0052cc"),
            state="disabled",
        )
        self.slider_pwm.pack(fill="x", pady=(6, 4))

        self.btn_pwm_set = ctk.CTkButton(
            pwm_frame,
            text="Apply PWM Speed",
            height=34,
            fg_color=self._c("accent_blue", "#0052cc"),
            hover_color=self._c("accent_blue", "#0052cc"),
            command=self._on_set_pwm,
            state="disabled",
            font=("Arial", 12, "bold"),
        )
        self.btn_pwm_set.pack(fill="x")

        self.lbl_pwm_hint = ctk.CTkLabel(
            parent,
            text="",
            font=("Arial", 11, "italic"),
            text_color=self._c("text_dim", "#6b7684"),
        )
        self.lbl_pwm_hint.pack(fill="x", padx=20, pady=(0, 8))

    # ==================================================================
    # UI event handlers (run on the Tkinter main thread)
    # ==================================================================
    def _on_connect(self) -> None:
        """Connect button handler: try to connect to the selected port."""
        self._set_busy(True)
        self._log("Connecting...")

        selection = self.opt_port.get()
        port = None if selection in ("Auto-detect", "", None) else selection

        def _worker() -> None:
            ok = self.controller.connect(port)
            self.after(0, lambda: self._on_connect_done(ok))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_connect_done(self, ok: bool) -> None:
        """UI-side completion of a connect attempt."""
        self._set_busy(False)
        if ok:
            self._log("Connected to ESP32.")
        else:
            self._log("Could not connect. Is the ESP32 plugged in?")

    def _on_disconnect(self) -> None:
        """Disconnect button handler."""
        self.controller.disconnect()
        self._log("Disconnected.")

    def _on_pump_on(self) -> None:
        """Pump ON button handler."""
        ok = self.controller.pump_on()
        self._log("Pump ON command sent." if ok else "Pump ON failed (not connected).")
        # Pump ON switches to manual mode -> refresh the slider availability.
        self._update_pwm_control_state()

    def _on_pump_off(self) -> None:
        """Pump OFF button handler."""
        ok = self.controller.pump_off()
        self._log("Pump OFF command sent." if ok else "Pump OFF failed (not connected).")
        # Pump OFF switches to manual mode -> refresh the slider availability.
        self._update_pwm_control_state()

    def _on_auto_mode(self) -> None:
        """AUTO Mode button handler."""
        ok = self.controller.auto_mode()
        self._log("AUTO mode command sent." if ok else "AUTO mode failed (not connected).")
        # PWM slider should only be available in MANUAL mode.
        self._update_pwm_control_state()

    def _on_pwm_slide(self, value) -> None:
        """Slider callback: update the PWM value label live."""
        try:
            val = int(float(value))
            self.lbl_pwm_set.configure(text=str(val))
        except Exception:
            pass

    def _on_set_pwm(self) -> None:
        """Set Speed button handler: send PWM=<value> command."""
        try:
            val = int(float(self.slider_pwm.get()))
        except Exception:
            val = 0
        ok = self.controller.set_pwm(val)
        self._log(
            f"Manual PWM set to {val}." if ok else "PWM set failed (not connected)."
        )
        self.lbl_pwm_set.configure(text=str(val))

    def _on_refresh_ports(self) -> None:
        """Refresh the port dropdown in a background thread."""
        self._log("Scanning serial ports...")
        self.btn_refresh.configure(state="disabled")
        threading.Thread(target=self._refresh_ports_worker, daemon=True).start()

    # ==================================================================
    # Background worker(s)
    # ==================================================================
    def _refresh_ports_worker(self) -> None:
        """Background: enumerate COM ports and populate the dropdown."""
        ports = self.controller.refresh_ports()
        values = ["Auto-detect"] + [p for p in ports if p]
        self.after(0, lambda: self._apply_port_list(values))

    def _apply_port_list(self, values: List[str]) -> None:
        """Apply the new port list to the dropdown (main thread)."""
        current = self.opt_port.get()
        self.opt_port.configure(values=values)
        if current in values:
            self.opt_port.set(current)
        else:
            self.opt_port.set("Auto-detect")
        self.btn_refresh.configure(state="normal")
        self._log(f"{len(values) - 1} port(s) found.")

    # ==================================================================
    # Controller callbacks (marshalled onto the main thread)
    # ==================================================================
    def _on_temp_data(
        self, inlet: float, outlet: float, pump: str, mode: str, flow_rate: float, pwm: int
    ) -> None:
        """Called from the serial reader thread -> schedule UI update."""
        try:
            self.after(
                0,
                lambda: self._update_temp_ui(inlet, outlet, pump, mode, flow_rate, pwm),
            )
        except Exception:
            pass

    def _on_connection_change(self, state: str, port: Optional[str]) -> None:
        """Called from a background thread -> schedule UI update."""
        try:
            self.after(0, lambda: self._update_connection_ui(state, port))
        except Exception:
            pass

    # ==================================================================
    # UI refresh helpers (main thread only)
    # ==================================================================
    def _update_pwm_control_state(self) -> None:
        """Enable the PWM slider only when connected AND in MANUAL mode."""
        manual = self._connected and (self._mode == "MANUAL")
        state = "normal" if manual else "disabled"
        try:
            self.slider_pwm.configure(state=state)
            self.btn_pwm_set.configure(state=state)
            hint = (
                "" if manual else
                ("Connect to the ESP32 to adjust pump speed." if not self._connected
                 else "Manual PWM control is active only in MANUAL mode.")
            )
            self.lbl_pwm_hint.configure(text=hint)
        except Exception:
            pass

    def _update_temp_ui(
        self, inlet: float, outlet: float, pump: str, mode: str, flow_rate: float, pwm: int
    ) -> None:
        """Refresh the readouts: temperatures, pump, mode, flow rate and PWM."""
        self._last_inlet = inlet
        self._last_outlet = outlet
        self._pump = pump
        self._mode = mode
        self._flow_rate = flow_rate/60
        self._pwm = pwm

        self.lbl_inlet.configure(text=f"{inlet:.2f} °C")
        self.lbl_outlet.configure(text=f"{outlet:.2f} °C")

        self.lbl_pump.configure(
            text=pump,
            text_color=self._c("accent_green", "#216e4e") if pump == "ON" else self._c("accent_red", "#ae2a19"),
        )
        self.lbl_mode.configure(
            text=mode,
            text_color=self._c("accent_blue", "#0052cc") if mode == "AUTO" else self._c("accent_orange", "#974f0c"),
        )
        self.lbl_flow.configure(
            text=f"{flow_rate:.2f} L/min",
            text_color=self._c("accent_blue", "#0052cc"),
        )
        self.lbl_pwm.configure(
            text=f"{int(pwm)} %",
            text_color=self._c("accent_orange", "#974f0c"),
        )
        # Sync the manual slider position with the live PWM value.
        try:
    # Only sync the slider while in AUTO mode
         if self._mode == "AUTO":
          self.slider_pwm.set(float(min(int(pwm), 255)))
          self.lbl_pwm_set.configure(text=str(int(pwm)))
        except Exception:
         pass
        # Slider availability follows the current mode.
        self._update_pwm_control_state()

        if outlet >= 45.0 and not self._buzzer_latched:
            self._buzzer_latched = True
            self._trigger_buzzer()
            self._log(f"High temperature alert: outlet at {outlet:.2f} °C.")
        elif outlet < 43.0:
            self._buzzer_latched = False

    def _update_connection_ui(self, state: str, port: Optional[str]) -> None:
        """Refresh the connection indicator and button states."""
        self._connected = state == "connected"
        self._port = port

        if self._connected:
            self.lbl_status.configure(
                text="● Connected",
                text_color=self._c("accent_green", "#216e4e"),
            )
            self.lbl_port.configure(text=f"Port: {port or '--'}")
            self.btn_connect.configure(state="disabled")
            self.btn_disconnect.configure(state="normal")
            self.btn_pump_on.configure(state="normal")
            self.btn_pump_off.configure(state="normal")
            self.btn_auto.configure(state="normal")
            self._log(f"Connected to ESP32 on {port}.")
        else:
            self.lbl_status.configure(
                text="● Disconnected",
                text_color=self._c("accent_red", "#ae2a19"),
            )
            self.lbl_port.configure(text="Port: --")
            self.btn_connect.configure(state="normal")
            self.btn_disconnect.configure(state="disabled")
            self.btn_pump_on.configure(state="disabled")
            self.btn_pump_off.configure(state="disabled")
            self.btn_auto.configure(state="disabled")
            self._buzzer_latched = False
            self._log("Disconnected.")
        # PWM control availability follows the connection state.
        self._update_pwm_control_state()

    def _trigger_buzzer(self) -> None:
        """Play a short alert sound when outlet temperature is too high."""
        try:
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            else:
                self.bell()
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        """Disable the connect/refresh buttons while a connect is in progress."""
        state = "disabled" if busy else "normal"
        self.btn_connect.configure(state=state)
        self.btn_refresh.configure(state=state)

    def _log(self, message: str) -> None:
        """Update the status message label and forward to host (best-effort)."""
        try:
            self.lbl_msg.configure(text=message)
        except Exception:
            pass
        if self._on_log is not None:
            try:
                self._on_log(message)
            except Exception:
                pass

    # ==================================================================
    # Lifecycle hooks (called by the host app)
    # ==================================================================
    def start(self) -> None:
        """Start the auto-reconnect worker."""
        if SerialManager.is_available():
            self.controller.start()
            self._log("Cooling system ready. Auto-detect is active.")
        else:
            self._log("pySerial not installed. Cooling system unavailable.")
            self.lbl_status.configure(text="● Unavailable", text_color=self._c("text_dim", "#6b7684"))

    def shutdown(self) -> None:
        """Stop all controller threads and close the CSV log."""
        try:
            self.controller.shutdown()
        except Exception:
            pass
