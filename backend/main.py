"""
DocRun — FastAPI backend

Endpoints:
  POST /api/parse          Upload a PDF, parse with Docling, extract fault table
  GET  /api/fault-table    Get the currently loaded fault table
  GET  /api/sensors        Current sensor snapshot
  POST /api/sensors/inject Inject a named fault scenario (for demo)
  DELETE /api/sensors/inject Clear injected faults (back to normal)
  POST /api/diagnose       Run one diagnostic pass right now
  WS   /ws/monitor         Continuous monitoring loop — pushes DiagnosticResult
                           every N seconds
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .pipeline.diagnostic_agent import run_diagnosis
from .pipeline.docling_parser import parse_document
from .pipeline.gemini_client import get_usage
from .pipeline.gemini_extractor import extract_troubleshooting_table
from .pipeline.schemas import CameraScanResult, DiagnosticResult, RobotActionStatus, TroubleshootingTable
from .pipeline.sensor_monitor import MockSensorMonitor
from .pipeline.serial_reader import SerialReader

load_dotenv()

# --------------------------------------------------------------------------- #
# App state                                                                   #
# --------------------------------------------------------------------------- #

logger = logging.getLogger(__name__)

# Serial reader for real hardware sensors (temperature, bearing_temp, humidity)
# Set SERIAL_PORT env var or pass --serial-port to override. Empty = disabled.
_serial_port = os.environ.get("SERIAL_PORT", "")

serial_reader: Optional[SerialReader] = None
if _serial_port:
    serial_reader = SerialReader(port=_serial_port)
    serial_reader.start()
    logger.info(f"Hardware serial reader enabled on {_serial_port}")
else:
    # Auto-detect common Linux serial ports
    for port_candidate in ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyUSB1", "/dev/ttyACM1"]:
        if Path(port_candidate).exists():
            serial_reader = SerialReader(port=port_candidate)
            serial_reader.start()
            logger.info(f"Hardware serial reader auto-detected on {port_candidate}")
            break
    if serial_reader is None:
        logger.info("No serial device found — using fully simulated sensors")

fault_table: Optional[TroubleshootingTable] = None
monitor = MockSensorMonitor(serial_reader=serial_reader)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

# In-memory diagnosis cache: rounded sensor hash → DiagnosticResult
_diagnosis_cache: dict[str, DiagnosticResult] = {}

# Robot replay state — only one replay at a time
_robot_status = RobotActionStatus()


def _save_json(filename: str, data: dict) -> None:
    """Save a dict as pretty-printed JSON to the output directory."""
    path = OUTPUT_DIR / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # nothing to set up / tear down yet


app = FastAPI(title="DocRun", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Document pipeline                                                            #
# --------------------------------------------------------------------------- #

@app.post("/api/parse", response_model=TroubleshootingTable)
async def parse_and_extract(file: UploadFile = File(...)):
    """
    Upload a PDF (or any Docling-supported format).
    Docling parses it, Gemini extracts the troubleshooting table.
    Results are cached by file hash — re-uploading the same PDF is instant.
    """
    global fault_table

    file_bytes = await file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    cache_path = CACHE_DIR / f"{file_hash}.json"

    # Check file-based cache
    if cache_path.exists():
        logger.info(f"Cache hit for {file.filename} ({file_hash[:12]})")
        cached = json.loads(cache_path.read_text())
        fault_table = TroubleshootingTable(**cached)
        monitor.set_active_sensors(fault_table.relevant_sensors, {k: v.model_dump() for k, v in fault_table.thresholds.items()})
        return fault_table

    suffix = Path(file.filename or "upload.pdf").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        loop = asyncio.get_running_loop()
        parsed = await loop.run_in_executor(None, parse_document, tmp_path)

        (OUTPUT_DIR / "parsed_markdown.md").write_text(parsed.markdown)

        fault_table = await loop.run_in_executor(
            None, extract_troubleshooting_table, parsed
        )
    finally:
        os.unlink(tmp_path)

    # Save to both output and cache
    ft_data = fault_table.model_dump()
    _save_json("fault_table.json", ft_data)
    cache_path.write_text(json.dumps(ft_data, ensure_ascii=False))
    logger.info(f"Cached parse result for {file.filename} ({file_hash[:12]})")

    monitor.set_active_sensors(fault_table.relevant_sensors, {k: v.model_dump() for k, v in fault_table.thresholds.items()})
    return fault_table


@app.get("/api/fault-table", response_model=TroubleshootingTable)
async def get_fault_table():
    if fault_table is None:
        raise HTTPException(status_code=404, detail="No document parsed yet. POST to /api/parse first.")
    monitor.set_active_sensors(fault_table.relevant_sensors, {k: v.model_dump() for k, v in fault_table.thresholds.items()})
    return fault_table


@app.get("/api/usage")
async def api_usage():
    """Gemini API usage stats for the current day."""
    return get_usage()


# --------------------------------------------------------------------------- #
# Sensor interface                                                             #
# --------------------------------------------------------------------------- #

@app.get("/api/sensors")
async def get_sensors():
    return monitor.get_snapshot()


@app.get("/api/scenarios")
async def get_scenarios():
    """Return available fault injection scenarios for currently active sensors."""
    return monitor.get_scenarios()


@app.post("/api/sensors/inject")
async def inject_fault(scenario: str):
    """Inject a named fault scenario for demo purposes."""
    scenarios = monitor.get_scenarios()
    if scenario not in scenarios:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{scenario}'. Valid: {list(scenarios)}",
        )
    monitor.clear_faults()
    for sensor, value in scenarios[scenario].items():
        monitor.inject_fault(sensor, value)
    _diagnosis_cache.clear()
    return {"injected": scenario, "overrides": scenarios[scenario]}


@app.delete("/api/sensors/inject")
async def clear_faults():
    monitor.clear_faults()
    _diagnosis_cache.clear()
    return {"status": "cleared"}


# --------------------------------------------------------------------------- #
# Diagnostic                                                                  #
# --------------------------------------------------------------------------- #

def _sensor_cache_key(snapshot) -> str:
    """Hash sensor readings rounded to 1 decimal so small jitter doesn't bust cache."""
    rounded = {name: round(r.value, 1) for name, r in snapshot.readings.items()}
    return hashlib.md5(json.dumps(rounded, sort_keys=True).encode()).hexdigest()


@app.post("/api/diagnose", response_model=DiagnosticResult)
async def diagnose():
    """Run one diagnostic pass against current sensor readings (cached by rounded values)."""
    if fault_table is None:
        raise HTTPException(status_code=404, detail="No document parsed yet.")
    snapshot = monitor.get_snapshot()

    cache_key = _sensor_cache_key(snapshot)
    if cache_key in _diagnosis_cache:
        logger.info(f"Diagnosis cache hit ({cache_key[:12]})")
        return _diagnosis_cache[cache_key]

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_diagnosis, fault_table, snapshot)

    _diagnosis_cache[cache_key] = result

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save_json(f"diagnosis_{ts}.json", {
        "sensors": snapshot.model_dump(),
        "diagnosis": result.model_dump(),
    })

    return result


# --------------------------------------------------------------------------- #
# Robot arm replay                                                             #
# --------------------------------------------------------------------------- #

_LEROBOT_CMD = [
    "/media/viktor/linuxssd/lerobot/.venv/bin/lerobot-replay",
    "--robot.type=so101_follower",
    "--robot.port=/dev/ttyACM0",
    "--robot.id=my_awesome_follower_arm",
    "--dataset.repo_id=local/circle_movement_02",
    "--dataset.root=/home/viktor/Downloads/circle_movement_02/circle_movement_02",
    "--dataset.episode=0",
]


@app.post("/api/robot-action", response_model=RobotActionStatus)
async def trigger_robot_action(action: str = ""):
    """Launch a lerobot-replay to physically execute a robot action."""
    global _robot_status
    import time as _time

    if _robot_status.state == "running":
        raise HTTPException(status_code=409, detail="A robot action is already running.")

    _robot_status = RobotActionStatus(
        state="running",
        action_label=action,
        started_at=_time.time(),
    )

    async def _run_replay():
        global _robot_status
        import time as _time
        try:
            env = {**os.environ, "HF_HUB_OFFLINE": "1"}
            proc = await asyncio.create_subprocess_exec(
                *_LEROBOT_CMD,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                _robot_status.state = "completed"
                _robot_status.completed_at = _time.time()
            else:
                _robot_status.state = "error"
                _robot_status.error_message = stderr.decode()[-500:]
                _robot_status.completed_at = _time.time()
        except Exception as e:
            _robot_status.state = "error"
            _robot_status.error_message = str(e)

    asyncio.create_task(_run_replay())
    return _robot_status


@app.get("/api/robot-status", response_model=RobotActionStatus)
async def get_robot_status():
    return _robot_status


@app.post("/api/robot-action/reset")
async def reset_robot_status():
    global _robot_status
    if _robot_status.state == "running":
        raise HTTPException(status_code=409, detail="Cannot reset while running.")
    _robot_status = RobotActionStatus()
    return {"status": "reset"}


# --------------------------------------------------------------------------- #
# Camera obstacle detection                                                    #
# --------------------------------------------------------------------------- #

@app.post("/api/camera/scan", response_model=CameraScanResult)
async def camera_scan():
    """Capture a single RealSense frame and detect obstacles."""
    try:
        from .pipeline.camera_detector import scan_for_obstacles
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, scan_for_obstacles)
        return result
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Camera dependencies not installed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Camera error: {e}")


# --------------------------------------------------------------------------- #
# WebSocket monitoring loop                                                   #
# --------------------------------------------------------------------------- #

@app.websocket("/ws/monitor")
async def monitor_ws(websocket: WebSocket, interval: float = 5.0):
    """
    Continuous monitoring loop.
    Every `interval` seconds: capture sensors, run diagnosis, push result.
    The front-end connects here for live updates.
    """
    await websocket.accept()
    if fault_table is None:
        await websocket.send_json({"error": "No document parsed yet."})
        await websocket.close()
        return

    try:
        while True:
            snapshot = monitor.get_snapshot()
            loop = asyncio.get_running_loop()
            result: DiagnosticResult = await loop.run_in_executor(
                None, run_diagnosis, fault_table, snapshot
            )
            await websocket.send_json({
                "sensors": snapshot.model_dump(),
                "diagnosis": result.model_dump(),
            })
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        pass


# --------------------------------------------------------------------------- #
# Static frontend (must be LAST so /api/* routes take priority)               #
# --------------------------------------------------------------------------- #

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
