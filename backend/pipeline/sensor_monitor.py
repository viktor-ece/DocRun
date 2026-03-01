"""
Sensor monitor — provides current sensor readings.

MockSensorMonitor runs with realistic baseline values and supports
fault injection for demo purposes. Swap this class out for a real
hardware reader (Modbus, serial, etc.) when connecting to physical sensors.

The MASTER POOL contains all possible sensors. After a document is parsed,
only sensors identified as relevant are activated.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .schemas import SensorReading, SensorSnapshot

if TYPE_CHECKING:
    from .serial_reader import SerialReader


# ── Master sensor pool ──
# All possible sensors with (baseline_value, unit).
# Only a subset will be active at any time depending on the parsed document.

_BASELINE: dict[str, tuple[float, str]] = {
    "temperature":            (42.0,    "°C"),      # ambient / coolant
    "bearing_temp":           (58.0,    "°C"),      # bearing housing
    "current":                (6.2,     "A"),       # motor current draw
    "voltage":                (231.0,   "V"),       # supply voltage
    "vibration":              (2.3,     "mm/s"),    # RMS vibration
    "speed":                  (1487.0,  "rpm"),     # shaft speed
    "power":                  (620.0,   "W"),       # power consumption
    "noise_db":               (48.0,    "dB(A)"),   # noise level at 1m
    "pressure":               (4.2,     "bar"),     # hydraulic / pneumatic
    "torque":                 (38.0,    "Nm"),      # shaft torque
    "flow_rate":              (12.5,    "L/min"),   # coolant / process flow
    "humidity":               (45.0,    "%RH"),     # ambient humidity
    "oil_level":              (78.0,    "%"),        # lubricant level
    "insulation_resistance":  (150.0,   "MΩ"),      # winding insulation
    "frequency":              (50.0,    "Hz"),       # supply frequency
}

_NOISE: dict[str, float] = {
    "temperature":            0.3,
    "bearing_temp":           0.5,
    "current":                0.1,
    "voltage":                0.8,
    "vibration":              0.2,
    "speed":                  5.0,
    "power":                  8.0,
    "noise_db":               0.5,
    "pressure":               0.1,
    "torque":                 0.5,
    "flow_rate":              0.3,
    "humidity":               0.8,
    "oil_level":              0.2,
    "insulation_resistance":  2.0,
    "frequency":              0.05,
}

# Safe range — noisy values clamped here so we never trigger alerts without injection
_SAFE_RANGE: dict[str, tuple[float, float]] = {
    "temperature":            (35.0,    43.0),
    "bearing_temp":           (50.0,    68.0),
    "current":                (5.0,     20.0),
    "voltage":                (220.0,   680.0),
    "vibration":              (1.0,     4.5),
    "speed":                  (750.0,   2950.0),
    "power":                  (550.0,   700.0),
    "noise_db":               (40.0,    63.0),
    "pressure":               (3.5,     5.0),
    "torque":                 (30.0,    45.0),
    "flow_rate":              (10.0,    15.0),
    "humidity":               (35.0,    55.0),
    "oil_level":              (70.0,    85.0),
    "insulation_resistance":  (120.0,   180.0),
    "frequency":              (49.8,    50.2),
}


# ── One fault scenario per sensor (value chosen to exceed typical thresholds) ──
_FAULT_VALUES: dict[str, float] = {
    "temperature":            55.0,
    "bearing_temp":           82.0,
    "current":                38.0,
    "voltage":                750.0,
    "vibration":              9.5,
    "speed":                  700.0,      # below min
    "power":                  25000.0,
    "noise_db":               74.0,
    "pressure":               8.5,
    "torque":                 75.0,
    "flow_rate":              2.0,         # below min
    "humidity":               85.0,
    "oil_level":              15.0,        # below min
    "insulation_resistance":  5.0,         # below min
    "frequency":              65.0,
}

# Default active sensors (before a document is parsed)
_DEFAULT_SENSORS = [
    "temperature", "bearing_temp", "current", "voltage",
    "vibration", "speed", "power", "noise_db",
]


class MockSensorMonitor:
    """
    Returns sensor snapshots with small Gaussian noise around baseline values.
    Only active sensors are included in snapshots.

    When thresholds are provided (from the parsed document), baselines and safe
    ranges are auto-calibrated so normal readings sit comfortably within the
    document's acceptable range. Fault injection values are set just beyond
    the document thresholds so they always trigger alerts.
    """

    def __init__(self, serial_reader: SerialReader | None = None) -> None:
        self._overrides: dict[str, SensorReading] = {}
        self._active: list[str] = list(_DEFAULT_SENSORS)
        self._serial: SerialReader | None = serial_reader
        # Per-sensor calibrated values (overridden when thresholds are provided)
        self._cal_baseline: dict[str, float] = {k: v[0] for k, v in _BASELINE.items()}
        self._cal_unit: dict[str, str] = {k: v[1] for k, v in _BASELINE.items()}
        self._cal_safe: dict[str, tuple[float, float]] = dict(_SAFE_RANGE)
        self._cal_fault: dict[str, float] = dict(_FAULT_VALUES)

    def set_active_sensors(
        self,
        names: list[str],
        thresholds: dict | None = None,
    ) -> None:
        """
        Set which sensors are active and calibrate to document thresholds.
        thresholds: {sensor_name: {"min": float|None, "max": float|None, "unit": str}}
        """
        valid = [n for n in names if n in _BASELINE]
        self._active = valid if valid else list(_DEFAULT_SENSORS)
        self._overrides = {k: v for k, v in self._overrides.items() if k in self._active}

        # Reset calibration to defaults
        self._cal_baseline = {k: v[0] for k, v in _BASELINE.items()}
        self._cal_unit = {k: v[1] for k, v in _BASELINE.items()}
        self._cal_safe = dict(_SAFE_RANGE)
        self._cal_fault = dict(_FAULT_VALUES)

        if not thresholds:
            return

        for name in self._active:
            if name not in thresholds:
                continue
            t = thresholds[name]
            t_min = t.get("min")
            t_max = t.get("max")
            t_unit = t.get("unit", "")

            # Use the document's unit for this sensor
            if t_unit:
                self._cal_unit[name] = t_unit

            if t_max is not None and t_min is not None:
                # Both bounds: baseline at 60% of range, safe range at 30-75%
                rng = t_max - t_min
                self._cal_baseline[name] = t_min + rng * 0.6
                self._cal_safe[name] = (t_min + rng * 0.3, t_min + rng * 0.75)
                self._cal_fault[name] = t_max * 1.15  # 15% above max
            elif t_max is not None:
                # Only max: baseline at 60% of max, safe at 40-75%
                self._cal_baseline[name] = t_max * 0.6
                self._cal_safe[name] = (t_max * 0.4, t_max * 0.75)
                self._cal_fault[name] = t_max * 1.15
            elif t_min is not None:
                # Only min: baseline at 150% of min, safe at 120-200%
                self._cal_baseline[name] = t_min * 1.5
                self._cal_safe[name] = (t_min * 1.2, t_min * 2.0)
                self._cal_fault[name] = t_min * 0.8  # below min

            # Noise: ~1% of baseline
            base = self._cal_baseline[name]
            _NOISE[name] = abs(base) * 0.01 if base != 0 else 0.1

    def get_active_sensors(self) -> list[str]:
        return list(self._active)

    def inject_fault(self, sensor: str, value: float) -> None:
        """Force a sensor to a specific value (to trigger a fault condition)."""
        if sensor not in _BASELINE:
            raise ValueError(f"Unknown sensor '{sensor}'. Valid: {list(_BASELINE)}")
        unit = self._cal_unit.get(sensor, _BASELINE[sensor][1])
        self._overrides[sensor] = SensorReading(value=value, unit=unit)

    def clear_faults(self) -> None:
        """Return all sensors to baseline noise."""
        self._overrides.clear()

    def get_snapshot(self) -> SensorSnapshot:
        # Get real hardware readings (if serial connected)
        hw_readings = self._serial.get_readings() if self._serial else {}

        readings: dict[str, SensorReading] = {}
        for name in self._active:
            if name not in _BASELINE:
                continue
            unit = self._cal_unit.get(name, _BASELINE[name][1])
            if name in self._overrides:
                # Fault injection takes highest priority
                readings[name] = self._overrides[name]
            elif name in hw_readings:
                # Real hardware data (from serial)
                readings[name] = SensorReading(value=round(hw_readings[name], 2), unit=unit)
            else:
                # Simulated with noise
                base = self._cal_baseline[name]
                noisy = base + random.gauss(0, _NOISE[name])
                lo, hi = self._cal_safe.get(name, _SAFE_RANGE.get(name, (base * 0.8, base * 1.2)))
                noisy = max(lo, min(hi, noisy))
                readings[name] = SensorReading(value=round(noisy, 2), unit=unit)
        return SensorSnapshot(readings=readings)

    def get_scenarios(self) -> dict[str, dict[str, float]]:
        """Return fault scenarios for currently active sensors only."""
        scenarios: dict[str, dict[str, float]] = {}
        for name in self._active:
            if name in self._cal_fault:
                label = f"high_{name}" if name not in ("speed", "flow_rate", "oil_level", "insulation_resistance") else f"low_{name}"
                scenarios[label] = {name: self._cal_fault[name]}
        scenarios["all_sensors"] = {
            name: self._cal_fault[name]
            for name in self._active
            if name in self._cal_fault
        }
        return scenarios
