"""
Gemini agent that extracts structured knowledge from a parsed Docling document.

Two things are extracted:
1. The troubleshooting table (fault entries with symptoms, causes, solutions)
2. Normal operating thresholds from specs / inspection checklists

For each fault entry the agent also infers which sensor readings would indicate
that symptom — connecting document knowledge to physical sensor world.
"""
from __future__ import annotations

import json

from .docling_parser import ParsedDocument
from .gemini_client import generate_json, get_client
from .schemas import FaultEntry, Threshold, TroubleshootingTable

_MODEL = "gemini-2.5-flash"

_MASTER_SENSORS = [
    "temperature", "bearing_temp", "current", "voltage", "vibration",
    "speed", "power", "noise_db", "pressure", "torque", "flow_rate",
    "humidity", "oil_level", "insulation_resistance", "frequency",
]

_EXTRACTION_PROMPT = """\
You are analyzing an industrial equipment operation & maintenance manual.

MASTER SENSOR LIST (use ONLY these names everywhere — never invent new names):
  [temperature, bearing_temp, current, voltage, vibration, speed, power, noise_db,
   pressure, torque, flow_rate, humidity, oil_level, insulation_resistance, frequency]

Your job is to extract:

1. TROUBLESHOOTING TABLE — every fault entry the document describes.

2. OPERATING THRESHOLDS — normal/acceptable ranges from the specs.
   CRITICAL: threshold keys MUST be from the master sensor list above. If the document
   has multiple values for one sensor category (e.g. "coolant temp max 115°C" and
   "thermostat open 87-91°C"), pick the single most useful operating range for that
   master sensor. Map document-specific parameters to the closest master sensor:
     - Any temperature (coolant, ambient, thermostat, IAT, exhaust) → "temperature"
     - Bearing/housing temperature → "bearing_temp"
     - Any current (starter, generator, charging, drain) → "current"
     - Any voltage (battery, charging, supply) → "voltage"
     - Any pressure (oil, fuel, coolant, hydraulic, pneumatic) → "pressure"
     - RPM / shaft speed / engine speed / cranking speed → "speed"
     - Power consumption / output → "power"
     - Vibration / oscillation → "vibration"
     - Noise / sound level → "noise_db"
     - Torque / tightening spec → "torque"
     - Flow rate (coolant, fuel, air) → "flow_rate"
     - Humidity → "humidity"
     - Oil level / lubricant level → "oil_level"
     - Insulation resistance / winding resistance → "insulation_resistance"
     - Frequency / Hz → "frequency"

3. SENSOR HINTS — for each fault entry, infer which sensor readings (and at
   what approximate level) would indicate that symptom is occurring.
   Use the thresholds you extracted and the fault description to reason this out.
   Express hints as short strings like "bearing_temp > 70", "vibration > 5",
   "current > rated_max", "voltage > 250". Use ONLY master sensor names.

4. ACTIONABLE STEPS — for each fault entry, translate the solution into actions.
   Every action MUST be prefixed with its category:
   - "software: " — parameter adjustments, restarts, mode changes, load reduction,
     threshold reconfiguration, any electronic/digital control
     (e.g. "software: reduce_speed 80%", "software: restart_cooling_system",
      "software: set_deceleration_time 10s", "software: reduce_load 70%")
   - "robot: " — physical movement of objects only (moving, clearing, removing debris)
     (e.g. "robot: clear_obstruction drive_shaft", "robot: remove_debris intake_filter")
   - "human: " — inspection, part replacement, lubrication, calibration, electrical work,
     measurement, anything requiring human judgment or dexterity
     (e.g. "human: inspect_bearings", "human: replace_fan_belt",
      "human: check_electrical_connections", "human: measure_insulation_resistance")
   Keep actions short and machine-readable. Every fault should have at least one action.

Document content (Docling structured markdown):
---
{markdown}
---

Respond with a single JSON object matching exactly this structure:
{{
  "document_title": "string",
  "equipment_type": "string (e.g. Thrust Bearing, Control Valve, Motor Drive)",
  "model": "string (model number if found, else empty string)",
  "relevant_sensors": ["sensor_name", ...],
  "thresholds": {{
    "<sensor_name>": {{"min": <number or null>, "max": <number or null>, "unit": "string"}}
  }},
  "faults": [
    {{
      "symptom": "string (exact text from document)",
      "possible_cause": "string",
      "solution": "string",
      "sensor_hints": ["string", ...],
      "actionable_steps": ["string (concrete physical action)", ...]
    }}
  ]
}}

IMPORTANT RULES:
- relevant_sensors: ONLY from the master list. Include 4-10 sensors that are mentioned,
  implied by faults, or realistic for this equipment. Always include sensors in thresholds.
- thresholds: keys MUST be from the master list (max 15 entries). One entry per sensor.
  Pick the most operationally useful range when the document has multiple values.
  Only include if the document explicitly states a numeric value.
- faults: include ALL fault rows from every troubleshooting table in the document.
- Do NOT invent sensor names outside the master list.
"""


def extract_troubleshooting_table(
    parsed: ParsedDocument,
    api_key: str | None = None,
) -> TroubleshootingTable:
    """
    Call Gemini to extract the structured troubleshooting table from
    a Docling-parsed document.
    """
    client = get_client(api_key)
    prompt = _EXTRACTION_PROMPT.format(markdown=parsed.markdown)
    raw = json.loads(generate_json(client, _MODEL, prompt))

    thresholds = {
        k: Threshold(**v) for k, v in raw.get("thresholds", {}).items()
    }
    faults = [FaultEntry(**f) for f in raw.get("faults", [])]

    return TroubleshootingTable(
        document_title=raw.get("document_title", parsed.title),
        equipment_type=raw.get("equipment_type", "Unknown"),
        model=raw.get("model", ""),
        relevant_sensors=raw.get("relevant_sensors", list(thresholds.keys())),
        thresholds=thresholds,
        faults=faults,
    )
