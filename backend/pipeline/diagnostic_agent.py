"""
Diagnostic agent — the core intelligence.

Gemini receives the full troubleshooting table (extracted from the document)
and the current sensor snapshot, then reasons across ALL readings simultaneously
to identify faults, disambiguate causes, and prescribe actions.

This is NOT a simple threshold check. Gemini understands that:
  - A single elevated reading might not be conclusive
  - Multiple readings together point to a specific fault vs. another
  - The solution and actions come directly from the document, not hardcoded logic
"""
from __future__ import annotations

import json

from .gemini_client import generate_json, get_client
from .schemas import DiagnosticResult, SensorSnapshot, TroubleshootingTable

_MODEL = "gemini-2.5-flash"

_DIAGNOSTIC_PROMPT = """\
You are an autonomous diagnostic agent for industrial equipment.

You have been given:
1. A structured troubleshooting table extracted from the equipment's maintenance manual.
2. A snapshot of current real-time sensor readings.

Your task: reason across ALL sensor readings simultaneously to determine whether
a fault condition is present, and if so, which one.

Important:
- Do NOT rely on a single sensor crossing a threshold. Consider the full pattern.
- Multiple faults can share similar symptoms — use sensor combinations to distinguish.
- A fault at medium confidence is better than silence. State your reasoning clearly.
- The "solution" and "recommended_actions" must come from the troubleshooting table,
  not from your general knowledge.
- Every recommended action MUST be prefixed with its category:
  - "software: " for electronic/digital actions (parameter adjustments, restarts, mode changes,
    load reduction, speed reduction, threshold reconfiguration)
  - "robot: " for ANY physical interaction with objects — clearing obstructions, removing debris,
    moving objects, checking/clearing mechanical blockages, cleaning filters, clearing jams.
    IMPORTANT: if the action involves physically touching, moving, or clearing something,
    it MUST be "robot:" even if it says "check" or "inspect" (e.g. "robot: clear_mechanical_obstructions")
  - "human: " ONLY for actions that strictly require human expertise — electrical work, part
    replacement, bearing lubrication, calibration, measurement with special tools

EQUIPMENT TROUBLESHOOTING TABLE:
{fault_table}

NORMAL OPERATING THRESHOLDS (from document specs):
{thresholds}

CURRENT SENSOR READINGS:
{sensor_snapshot}

Respond with a JSON object matching exactly this structure:
{{
  "fault_detected": <true|false>,
  "matched_symptom": "<symptom string from table, or null>",
  "possible_cause": "<cause string from table, or null>",
  "solution": "<solution string from table, or null>",
  "confidence": "<high|medium|low|none>",
  "reasoning": "<detailed multi-sentence explanation of which sensors contributed,
                 why this fault was chosen over others, and any ambiguities>",
  "recommended_actions": ["<action 1>", "<action 2>", ...],
  "sensor_evidence": {{
    "<sensor_name>": "<one sentence: current value, normal range, and why it matters>"
  }}
}}

If no fault is detected, set fault_detected to false, matched_symptom/cause/solution
to null, confidence to "none", and explain in reasoning why all readings look normal.
"""


def run_diagnosis(
    fault_table: TroubleshootingTable,
    snapshot: SensorSnapshot,
    api_key: str | None = None,
) -> DiagnosticResult:
    """
    Run one diagnostic pass: reason across sensor readings + fault table.
    Returns a structured DiagnosticResult.
    """
    client = get_client(api_key)

    # Serialise context for the prompt
    fault_table_str = json.dumps(
        [f.model_dump() for f in fault_table.faults], indent=2
    )
    thresholds_str = json.dumps(
        {k: v.model_dump() for k, v in fault_table.thresholds.items()}, indent=2
    )
    snapshot_str = json.dumps(
        {
            name: {"value": r.value, "unit": r.unit}
            for name, r in snapshot.readings.items()
        },
        indent=2,
    )

    prompt = _DIAGNOSTIC_PROMPT.format(
        fault_table=fault_table_str,
        thresholds=thresholds_str,
        sensor_snapshot=snapshot_str,
    )

    raw = json.loads(generate_json(client, _MODEL, prompt))
    return DiagnosticResult(**raw)
