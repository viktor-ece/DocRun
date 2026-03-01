from __future__ import annotations
import time
from typing import Optional
from pydantic import BaseModel, Field


class Threshold(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    unit: str = ""


class FaultEntry(BaseModel):
    symptom: str
    possible_cause: str
    solution: str
    sensor_hints: list[str] = Field(default_factory=list)
    actionable_steps: list[str] = Field(default_factory=list)


class TroubleshootingTable(BaseModel):
    document_title: str
    equipment_type: str
    model: str
    # Normal operating thresholds extracted from specs / inspection checklist
    # e.g. {"bearing_temp": Threshold(max=70, unit="°C")}
    thresholds: dict[str, Threshold] = Field(default_factory=dict)
    relevant_sensors: list[str] = Field(default_factory=list)
    faults: list[FaultEntry]


class SensorReading(BaseModel):
    value: float
    unit: str


class SensorSnapshot(BaseModel):
    readings: dict[str, SensorReading]
    timestamp: float = Field(default_factory=time.time)


class DiagnosticResult(BaseModel):
    fault_detected: bool
    matched_symptom: Optional[str] = None
    possible_cause: Optional[str] = None
    solution: Optional[str] = None
    confidence: str = "none"  # "high" | "medium" | "low" | "none"
    reasoning: str
    recommended_actions: list[str] = Field(default_factory=list)
    # Which sensors contributed to the diagnosis and why
    sensor_evidence: dict[str, str] = Field(default_factory=dict)


class RobotActionStatus(BaseModel):
    state: str = "idle"  # "idle" | "running" | "completed" | "error"
    action_label: str = ""
    error_message: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class ObstacleDetail(BaseModel):
    x: int
    y: int
    width: int
    height: int
    distance_m: float
    area_px: int


class CameraScanResult(BaseModel):
    detected: bool
    obstacle_count: int = 0
    obstacles: list[ObstacleDetail] = Field(default_factory=list)
    image_base64: str = ""
