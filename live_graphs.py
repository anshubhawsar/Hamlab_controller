"""
live_graphs.py
==============

Live Graph Dashboard for the HAM Lab cooling system.

A Matplotlib-based, real-time plotting panel embedded in a Tkinter frame.
It renders three subplots that scroll with time:

    * Temperature - Inlet Temp, Outlet Temp and dT (dT = outlet - inlet)
    * Flow        - Flow Rate (L/min)
    * Pump        - PWM duty cycle and Pump Speed (0..100%)

Data is buffered in memory for a rolling window of the last 10 minutes
(600 seconds) at a 1 Hz update cadence. The figure auto-refreshes on every
:meth:`update` call and provides the standard Matplotlib navigation toolbar
(zoom, pan, home/reset) as well as Save-as-PNG and Export-PDF helpers.

The module is deliberately UI-agnostic about the theme: it accepts a simple
``colors`` dictionary (like the rest of the CoolingPanel) so it reuses the
host app's light/dark palette.
"""

from __future__ import annotations

import os
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# Matplotlib imports (kept local so the module import fails gracefully on
# systems where matplotlib is not installed).
# ---------------------------------------------------------------------------
try:
    import matplotlib

    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates

    _MPL_AVAILABLE = True
except Exception:  # pragma: no cover - non-graphical fallback
    _MPL_AVAILABLE = False
    FigureCanvasTkAgg = None  # type: ignore
    NavigationToolbar2Tk = None  # type: ignore
    Figure = None  # type: ignore
    mdates = None  # type: ignore

#: Rolling window length (seconds) kept in the live buffers.
GRAPH_WINDOW_SECONDS = 600  # 10 minutes

#: Update interval (seconds) used by the calling GUI to refresh the figure.
GRAPH_UPDATE_INTERVAL_MS = 1000


class LiveGraphDashboard:
    """A scrollable, auto-updating Matplotlib dashboard for the cooling data.

    Args:
        master: Parent Tkinter widget.
        colors: Optional theme color dict (``bg_secondary``, ``text_primary``,
            ``accent_blue``, ``accent_orange``, ``accent_green`` ...).
        window_seconds: Rolling time window in seconds (default 600).
        max_points: Optional hard cap on buffered points (default derived
            from the window at 1 Hz).
    """

    #: Fallback palette used when no ``colors`` dict is provided.
    _DEFAULT_COLORS = {
        "accent_blue": "#0052cc",
        "accent_orange": "#974f0c",
        "accent_green": "#216e4e",
        "accent_red": "#ae2a19",
        "accent_purple": "#6c2bd9",
        "accent_teal": "#0f766e",
        "bg_secondary": "#ffffff",
        "text_primary": "#0d1b2a",
        "text_secondary": "#3d4756",
        "grid_color": "#d1d8e0",
        "plot_bg": "#fafbfc",
    }

    def __init__(
        self,
        master,
        colors: Optional[Dict[str, str]] = None,
        window_seconds: int = GRAPH_WINDOW_SECONDS,
        max_points: Optional[int] = None,
    ) -> None:
        if not _MPL_AVAILABLE:
            raise RuntimeError(
                "Matplotlib is not available. Install it with: pip install matplotlib"
            )

        self._colors = dict(self._DEFAULT_COLORS)
        if colors:
            self._colors.update(colors)

        self._window_seconds = max(1, int(window_seconds))
        self._max_points = max_points or (self._window_seconds + 10)

        # --- rolling data buffers (list-like with a max length) ---
        # Each entry is (timestamp: datetime, value: float).
        self._times: Deque[datetime] = deque(maxlen=self._max_points)
        self._inlet: Deque[float] = deque(maxlen=self._max_points)
        self._outlet: Deque[float] = deque(maxlen=self._max_points)
        self._delta: Deque[float] = deque(maxlen=self._max_points)
        self._flow: Deque[float] = deque(maxlen=self._max_points)
        self._pwm: Deque[float] = deque(maxlen=self._max_points)

        self._canvas = None
        self._toolbar = None

        self._build_figure(master)

    # ------------------------------------------------------------------
    # Figure construction
    # ------------------------------------------------------------------
    def _build_figure(self, master) -> None:
        """Create the Matplotlib figure, three subplots and the canvas."""
        text_primary = self._colors["text_primary"]
        text_secondary = self._colors["text_secondary"]
        bg = self._colors["bg_secondary"]
        plot_bg = self._colors["plot_bg"]
        grid_color = self._colors["grid_color"]

        self._fig = Figure(figsize=(9.5, 7.2), dpi=100, facecolor=bg)
        self._fig.subplots_adjust(
            left=0.08, right=0.96, top=0.93, bottom=0.09, hspace=0.42
        )

        # --- Temperature subplot ---
        ax_temp = self._fig.add_subplot(3, 1, 1)
        ax_temp.set_facecolor(plot_bg)
        (self._line_inlet,) = ax_temp.plot(
            [], [], color=self._colors["accent_blue"], lw=1.8,
            label="Inlet Temp",
        )
        (self._line_outlet,) = ax_temp.plot(
            [], [], color=self._colors["accent_orange"], lw=1.8,
            label="Outlet Temp",
        )
        (self._line_delta,) = ax_temp.plot(
            [], [], color=self._colors["accent_purple"], lw=1.4,
            linestyle="--", label="dT (out-in)",
        )
        ax_temp.set_title("Temperature (°C)", fontsize=13, fontweight="bold",
                          color=text_primary)
        ax_temp.set_ylabel("°C", fontsize=13, color=text_secondary)
        ax_temp.grid(True, color=grid_color, linewidth=0.6, alpha=0.7)
        ax_temp.legend(loc="upper left", fontsize=10, framealpha=0.9)
        ax_temp.tick_params(colors=text_secondary, labelsize=12)
        self._format_time_axis(ax_temp)

        # --- Flow subplot ---
        ax_flow = self._fig.add_subplot(3, 1, 2)
        ax_flow.set_facecolor(plot_bg)
        (self._line_flow,) = ax_flow.plot(
            [], [], color=self._colors["accent_teal"], lw=1.8,
            label="Flow Rate",
        )
        ax_flow.set_title("Flow Rate (L/min)", fontsize=13, fontweight="bold",
                          color=text_primary)
        ax_flow.set_ylabel("L/min", fontsize=13, color=text_secondary)
        ax_flow.grid(True, color=grid_color, linewidth=0.6, alpha=0.7)
        ax_flow.legend(loc="upper left", fontsize=10, framealpha=0.9)
        ax_flow.tick_params(colors=text_secondary, labelsize=12)
        self._format_time_axis(ax_flow)

        # --- Pump subplot (PWM + speed) ---
        ax_pump = self._fig.add_subplot(3, 1, 3)
        ax_pump.set_facecolor(plot_bg)
        (self._line_pwm,) = ax_pump.plot(
            [], [], color=self._colors["accent_red"], lw=1.8,
            label="PWM (0-255)",
        )
        (self._line_speed,) = ax_pump.plot(
            [], [], color=self._colors["accent_green"], lw=1.4,
            linestyle="-", label="Pump Speed (%)",
        )
        ax_pump.set_title("Pump - PWM & Speed", fontsize=13, fontweight="bold",
                          color=text_primary)
        ax_pump.set_ylabel("", fontsize=13, color=text_secondary)
        ax_pump.set_xlabel("Time", fontsize=13, color=text_secondary)
        ax_pump.grid(True, color=grid_color, linewidth=0.6, alpha=0.7)
        ax_pump.legend(loc="upper left", fontsize=10, framealpha=0.9)
        ax_pump.tick_params(colors=text_secondary, labelsize=12)
        self._format_time_axis(ax_pump)

        # Keep references for the update step.
        self._ax_temp = ax_temp
        self._ax_flow = ax_flow
        self._ax_pump = ax_pump

        # --- Canvas + toolbar ---
        self._canvas = FigureCanvasTkAgg(self._fig, master=master)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        self._toolbar = NavigationToolbar2Tk(self._canvas, master)
        self._toolbar.update()

        self._redraw_canvas()

    @staticmethod
    def _format_time_axis(ax) -> None:
        """Configure a shared time formatter for an axis."""
        if mdates is None:
            return
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

    # ------------------------------------------------------------------
    # Data feed
    # ------------------------------------------------------------------
    def add_reading(
        self,
        inlet: float,
        outlet: float,
        flow_rate: float,
        pwm: int,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Append a single telemetry sample to the rolling buffers.

        Args:
            inlet: Inlet temperature in °C.
            outlet: Outlet temperature in °C.
            flow_rate: Flow rate in L/min.
            pwm: PWM duty cycle (0..255).
            timestamp: Optional sample time; defaults to ``datetime.now()``.
        """
        now = timestamp or datetime.now()
        delta = outlet - inlet

        self._times.append(now)
        self._inlet.append(float(inlet))
        self._outlet.append(float(outlet))
        self._delta.append(float(delta))
        self._flow.append(float(flow_rate))
        self._pwm.append(float(pwm))

        # Keep the rolling window inside the requested time range.
        if self._times and self._window_seconds > 0:
            cutoff = now.timestamp() - self._window_seconds
            while self._times and self._times[0].timestamp() < cutoff:
                self._times.popleft()
                self._inlet.popleft()
                self._outlet.popleft()
                self._delta.popleft()
                self._flow.popleft()
                self._pwm.popleft()

    def update(self) -> None:
        """Redraw the figure with the latest buffered data (called ~1/sec)."""
        if not _MPL_AVAILABLE or len(self._times) < 2:
            return
        times = list(self._times)

        self._line_inlet.set_data(times, list(self._inlet))
        self._line_outlet.set_data(times, list(self._outlet))
        self._line_delta.set_data(times, list(self._delta))
        self._line_flow.set_data(times, list(self._flow))
        self._line_pwm.set_data(times, list(self._pwm))
        self._line_speed.set_data(times, [min(100.0, (v / 255.0) * 100.0)
                                          for v in self._pwm])

        if times:
            self._rescale_x(self._ax_temp, times)
            self._rescale_x(self._ax_flow, times)
            self._rescale_x(self._ax_pump, times)

        self._redraw_canvas()

    @staticmethod
    def _rescale_x(ax, times: List) -> None:
        """Auto-scale the x-axis to the buffered time range."""
        try:
            import matplotlib.dates as _md

            lo = _md.date2num(times[0])
            hi = _md.date2num(times[-1])
            if hi - lo < 1e-9:
                hi += (5.0 / 86400.0)  # ensure a tiny window
            ax.set_xlim(lo, hi)
        except Exception:
            pass

    def _redraw_canvas(self) -> None:
        """Draw the canvas (safe no-op when canvas is missing)."""
        try:
            self._fig.tight_layout()
            self._canvas.draw()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Centering helpers for new datapoints (auto-rescale y too)
    # ------------------------------------------------------------------
    def autoscale(self) -> None:
        """Re-enable auto-scaling on all subplots (useful after zoom/pan)."""
        for ax in (self._ax_temp, self._ax_flow, self._ax_pump):
            try:
                ax.relim()
                ax.autoscale_view()
            except Exception:
                pass
        self._redraw_canvas()

    def clear(self) -> None:
        """Clear all buffered data and reset the axes."""
        self._times.clear()
        self._inlet.clear()
        self._outlet.clear()
        self._delta.clear()
        self._flow.clear()
        self._pwm.clear()
        for ax in (self._ax_temp, self._ax_flow, self._ax_pump):
            try:
                ax.relim()
                ax.autoscale_view()
            except Exception:
                pass
        self._redraw_canvas()

    # ------------------------------------------------------------------
    # Export / save helpers
    # ------------------------------------------------------------------
    def save_as_png(self, path: str) -> str:
        """Save the current figure as a PNG file.

        Args:
            path: Destination path (e.g. ``~/graphs/temps.png``).

        Returns:
            The path actually written.
        """
        return self._save(path, "png")

    def export_pdf(self, path: str) -> str:
        """Export the current figure to a PDF file.

        Args:
            path: Destination path (e.g. ``~/graphs/temps.pdf``).

        Returns:
            The path actually written.
        """
        return self._save(path, "pdf")

    def _save(self, path: str, fmt: str) -> str:
        """Shared save routine for PNG/PDF export."""
        if not _MPL_AVAILABLE:
            raise RuntimeError("Matplotlib is not available.")
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fig.savefig(path, format=fmt, dpi=120, bbox_inches="tight")
        return path

    def get_window_seconds(self) -> int:
        """Return the configured rolling window (seconds)."""
        return self._window_seconds

    def get_point_count(self) -> int:
        """Return the number of buffered samples."""
        return len(self._times)

    def get_axes(self):
        """Return the three matplotlib Axes (for advanced users/testing)."""
        return self._ax_temp, self._ax_flow, self._ax_pump

    def is_available(self) -> bool:
        """Return True when Matplotlib is available."""
        return _MPL_AVAILABLE
