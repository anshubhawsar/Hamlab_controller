"""
ai_learning.py
==============

AI Learning Module - Reinforcement Learning Data Collection.

Purpose
-------
At this stage, reinforcement learning does NOT control the pump. Instead,
this module continuously collects operational data from the cooling system
and builds a labelled dataset that will later be used to train an RL model
offline in Python.

Every second a row is appended to a session CSV file containing:

    Timestamp, Experiment ID, Material Type, Material Thickness,
    Inlet Temperature, Outlet Temperature, Temperature Difference (dT),
    Flow Rate, Pump PWM, Pump Status, Operating Mode, Alarm Status,
    Cooling Efficiency, User Notes

The module is UI-agnostic and thread-safe: it pulls state snapshots from a
``CoolingController`` (via ``get_state()``) on a background daemon thread and
writes them to disk. It never reads the serial port directly and never
controls the pump.
"""

from __future__ import annotations

import csv
import os
import threading
from datetime import datetime
from typing import Dict, Optional

#: Directory (under the project base) where AI training CSVs are stored.
AI_DATA_DIR_NAME = "ai_training_data"
LOGS_DIR_NAME = "logs"

#: CSV header / column order for the training dataset.
CSV_HEADER = [
    "timestamp",
    "experiment_id",
    "material",
    "thickness_mm",
    "inlet_temp_c",
    "outlet_temp_c",
    "delta_t_c",
    "flow_lpm",
    "pwm",
    "pump",
    "mode",
    "alarm",
    "efficiency",
    "notes",
]

#: Outlet temperature threshold (°C) that triggers a HIGH_TEMP alarm.
#: Mirrors the GUI buzzer threshold in the cooling panel.
HIGH_TEMP_THRESHOLD_C = 45.0


def _resolve_ai_dir() -> str:
    """Return the absolute path of the AI training data directory."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, LOGS_DIR_NAME, AI_DATA_DIR_NAME)


def compute_efficiency(outlet_c: float, inlet_c: float) -> float:
    """Compute a simple cooling efficiency metric (percent).

    Efficiency is modelled as the fraction of the inlet-to-outlet temperature
    rise that the cooling system removes relative to the outlet temperature.
    A higher outlet rise relative to inlet indicates more heat being carried
    away; we clamp the result to a sensible 0..100 range.

    Args:
        outlet_c: Outlet temperature in °C.
        inlet_c: Inlet temperature in °C.

    Returns:
        Efficiency in percent (0..100).
    """
    if outlet_c <= 0:
        return 0.0
    delta = outlet_c - inlet_c
    # Positive delta means the coolant absorbed heat -> higher efficiency.
    eff = (delta / outlet_c) * 100.0
    if eff < 0:
        eff = 0.0
    if eff > 100:
        eff = 100.0
    return round(eff, 2)


def compute_alarm(outlet_c: float) -> str:
    """Return the alarm status string for a given outlet temperature.

    Returns ``"HIGH_TEMP"`` when the outlet reaches/exceeds the high-temp
    threshold, otherwise ``"NONE"``.
    """
    if outlet_c >= HIGH_TEMP_THRESHOLD_C:
        return "HIGH_TEMP"
    return "NONE"


class AILearningCollector:
    """Background collector that builds an RL training dataset.

    Args:
        controller: A ``CoolingController`` instance exposing ``get_state()``.
        on_status: Optional callback ``(message: str)`` invoked (from the
            background thread) whenever collection starts/stops or a row is
            written. Useful for UI updates.
    """

    def __init__(self, controller, on_status: Optional[callable] = None) -> None:
        self._controller = controller
        self._on_status = on_status

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # --- session metadata (set by the UI before starting) ---
        self._experiment_id: str = "EXP-001"
        self._material: str = "Al6061"
        self._thickness_mm: str = "8"
        self._notes: str = ""

        # --- active session state ---
        self._running = False
        self._csv_path: Optional[str] = None
        self._csv_file = None
        self._csv_writer = None
        self._sample_count = 0

    # ------------------------------------------------------------------
    # Metadata setters (call before start())
    # ------------------------------------------------------------------
    def set_experiment_id(self, value: str) -> None:
        """Set the experiment identifier (e.g. ``EXP-001``)."""
        with self._lock:
            self._experiment_id = (value or "EXP-001").strip() or "EXP-001"

    def set_material(self, value: str) -> None:
        """Set the material type (e.g. ``Al6061``)."""
        with self._lock:
            self._material = (value or "Al6061").strip() or "Al6061"

    def set_thickness(self, value: str) -> None:
        """Set the material thickness in mm (free-text)."""
        with self._lock:
            self._thickness_mm = (value or "8").strip() or "8"

    def set_notes(self, value: str) -> None:
        """Set optional user notes captured with each row."""
        with self._lock:
            self._notes = value or ""

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start the background collector (one row per second).

        Returns:
            True when the collector was started this call; False if it was
            already running or could not be started.
        """
        with self._lock:
            if self._running:
                return False

            try:
                self._open_csv()
            except Exception as exc:
                self._notify(f"AI data collection failed to open file: {exc}")
                return False

            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="AILearningCollector",
                daemon=True,
            )
            self._thread.start()
            self._notify(f"AI collection started -> {os.path.basename(self._csv_path or '')}")
            return True

    def stop(self) -> None:
        """Stop the background collector and close the CSV file."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

        if self._thread is not None:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
            self._thread = None

        self._close_csv()
        self._notify(
            f"AI collection stopped ({self._sample_count} samples -> "
            f"{os.path.basename(self._csv_path or '')})"
        )

    def is_running(self) -> bool:
        """Return True when the collector is currently running."""
        with self._lock:
            return self._running

    def get_session_info(self) -> Dict[str, object]:
        """Return a snapshot of the current session (path, count, running)."""
        with self._lock:
            return {
                "running": self._running,
                "csv_path": self._csv_path,
                "sample_count": self._sample_count,
                "experiment_id": self._experiment_id,
                "material": self._material,
                "thickness_mm": self._thickness_mm,
            }

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Sample the controller state once per second and append a row."""
        while not self._stop_event.is_set():
            try:
                self._collect_one_row()
            except Exception as exc:
                self._notify(f"AI data collection error: {exc}")

            # Sleep in small slices so stop() is responsive.
            deadline = datetime.now().timestamp() + 1.0
            while datetime.now().timestamp() < deadline and not self._stop_event.is_set():
                self._stop_event.wait(0.1)

    def _collect_one_row(self) -> None:
        """Read the controller state and append a single CSV row."""
        state = self._controller.get_state()

        inlet = float(state.get("inlet_temp") or 0.0)
        outlet = float(state.get("outlet_temp") or 0.0)
        flow = float(state.get("flow_rate") or 0.0)
        pwm = int(state.get("pwm") or 0)
        pump = str(state.get("pump") or "OFF")
        mode = str(state.get("mode") or "MANUAL")

        delta_t = outlet - inlet
        efficiency = compute_efficiency(outlet, inlet)
        alarm = compute_alarm(outlet)

        with self._lock:
            experiment_id = self._experiment_id
            material = self._material
            thickness = self._thickness_mm
            notes = self._notes

            if self._csv_writer is None:
                return

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [
                timestamp,
                experiment_id,
                material,
                thickness,
                f"{inlet:.2f}",
                f"{outlet:.2f}",
                f"{delta_t:.2f}",
                f"{flow:.2f}",
                str(pwm),
                pump,
                mode,
                alarm,
                f"{efficiency:.2f}",
                notes,
            ]
            try:
                self._csv_writer.writerow(row)
                self._csv_file.flush()
                self._sample_count += 1
            except Exception:
                pass

    # ------------------------------------------------------------------
    # CSV helpers
    # ------------------------------------------------------------------
    def _open_csv(self) -> None:
        """Create a new per-session CSV file and write the header row."""
        ai_dir = _resolve_ai_dir()
        os.makedirs(ai_dir, exist_ok=True)

        with self._lock:
            experiment_id = self._experiment_id
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Sanitise the experiment id for use in a filename.
            safe_exp = "".join(c for c in experiment_id if c.isalnum() or c in ("-", "_")) or "EXP"
            filename = f"{safe_exp}_{timestamp}.csv"
            path = os.path.join(ai_dir, filename)

            self._csv_file = open(path, "a", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(CSV_HEADER)
            self._csv_file.flush()
            self._csv_path = path
            self._sample_count = 0

    def _close_csv(self) -> None:
        """Close the CSV file handle (best-effort)."""
        with self._lock:
            if self._csv_file is not None:
                try:
                    self._csv_file.close()
                except Exception:
                    pass
                self._csv_file = None
                self._csv_writer = None
            self._csv_path = None

    def _notify(self, message: str) -> None:
        """Invoke the status callback (best-effort, never raises)."""
        if self._on_status is None:
            return
        try:
            self._on_status(message)
        except Exception:
            pass

    def shutdown(self) -> None:
        """Stop collection and release resources (call on app exit)."""
        self.stop()
