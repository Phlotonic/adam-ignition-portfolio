"""
ai_anomaly_agent.py

Production-Grade FastAPI Microservice for Real-Time Triple-Bottom-Line
Regenerative Data Center Telemetry Ingestion and Anomaly Detection.

Architecture:
  1. MQTT Subscriber (Background Thread):
     - Consumes Sparkplug B / JSON telemetry from `spBv1.0/DataCenter/NDATA/+`.
     - Supports Paho-MQTT 2.x & 1.x callback protocol with auto-reconnect.
  2. Asynchronous TimescaleDB Ingest Queue:
     - Buffers telemetry points to prevent blocking MQTT network I/O.
     - Performs async batch inserts into `ecosystem_telemetry` via asyncpg pool.
     - Resilient: continues in-memory analysis even if database is temporarily unavailable.
  3. Ensemble Anomaly Detection Engine:
     - Deterministic Thresholds: Critical Heat Flux (CHF limit ~170 W/cm2) and SLA balance checks.
     - Dual Isolation Forests: Resolves temporal impedance mismatch between continuous
       thermodynamic physics (seconds) and discrete economic settlement events (hours).
  4. Ignition 8.3 Event Streams Forwarding:
     - Optionally posts detected anomalies to Ignition Event Streams webhook.
  5. REST API (MCP-Over-HTTP Tools):
     - /health, /status, /anomalies, /pod/{pod_id}, /acknowledge/{anomaly_id}
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional
import uuid
from collections import deque

import asyncpg
import numpy as np
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sklearn.ensemble import IsolationForest
import urllib.request
import urllib.error

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("ecosystem_agent")

# =============================================================================
# CONFIGURATION
# =============================================================================
BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC: str = os.getenv("MQTT_TOPIC", "spBv1.0/DataCenter/NDATA/+")

DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")
# Convert SQLAlchemy-style asyncpg URI if present: postgresql+asyncpg:// -> postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

IGNITION_WEBHOOK_URL: Optional[str] = os.getenv("IGNITION_WEBHOOK_URL", "")

BUFFER_SIZE: int = int(os.getenv("ANOMALY_BUFFER_SIZE", "100"))
IF_FIT_THRESHOLD: int = int(os.getenv("ANOMALY_IF_FIT_THRESHOLD", "50"))
IF_CONTAMINATION: float = float(os.getenv("ANOMALY_IF_CONTAMINATION", "0.05"))

# Physics: HKUST-1 Two-Phase Immersion Limits (CHF on copper micro-mesh is ~170 W/cm2)
THRESHOLD_CHF_WARN: float = 150.0  # W/cm2 — approaching nucleate boiling limit
THRESHOLD_CHF_CRIT: float = 165.0  # W/cm2 — dry-out film boiling imminent
THRESHOLD_FLUID_WARN: float = 60.0 # °C — optimal desorption temperature
THRESHOLD_FLUID_CRIT: float = 65.0 # °C — excessive vapor superheat

# Economics: Smart Contract & Payout SLA Limits
THRESHOLD_UNSETTLED_WARN: float = 500.0   # USD unsettled balance warning
THRESHOLD_UNSETTLED_CRIT: float = 1000.0  # USD unsettled balance SLA breach

# =============================================================================
# PYDANTIC DATA MODELS
# =============================================================================

class EcosystemTelemetry(BaseModel):
    """Telemetry linking thermodynamics, hydrology, and economics."""
    heat_flux_w_cm2: float = Field(..., description="Heat flux across the HKUST-1 wick (W/cm2)")
    fluid_temp_c: float = Field(..., description="Dielectric bulk fluid temperature (°C)")
    vapor_pressure_kpa: float = Field(..., description="Boiling dielectric vapor pressure (kPa)")
    water_harvested_liters: float = Field(..., description="Water harvested by MOF-303 desiccant (L)")
    compute_yield_usd: float = Field(..., description="Current compute workload value (USD)")
    unsettled_balance_usd: float = Field(..., description="Accumulated unsettled balance for participant (USD)")
    participant_id: Optional[str] = Field("default_operator", description="Participant or wallet address")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp of the measurement")


class PodStatus(BaseModel):
    """Complete status summary for a single immersion pod ecosystem."""
    pod_id: str
    latest_metrics: EcosystemTelemetry
    sample_count: int
    phys_model_fitted: bool
    econ_model_fitted: bool
    active_anomalies: List[str]
    status: str


class Anomaly(BaseModel):
    """A single detected physical or economic anomaly event."""
    anomaly_id: str
    pod_id: str
    metric: str
    value: float
    threshold: Optional[float]
    severity: str
    method: str
    detected_at: str
    acknowledged: bool


class FacilityStatus(BaseModel):
    """Top-level facility health summary."""
    facility: str
    pod_count: int
    active_anomaly_count: int
    pods: List[PodStatus]
    overall_status: str
    last_updated: str


class AcknowledgeResponse(BaseModel):
    anomaly_id: str
    acknowledged: bool
    message: str


class HealthResponse(BaseModel):
    status: str
    service: str
    db_connected: bool
    mqtt_connected: bool
    timestamp: str


# =============================================================================
# IN-MEMORY THREAD-SAFE STATE STORE
# =============================================================================

class AgentState:
    """Thread-safe store shared between MQTT subscriber, DB worker, and FastAPI."""
    def __init__(self) -> None:
        self.buffers: Dict[str, deque] = {}
        self.phys_models: Dict[str, Optional[IsolationForest]] = {}
        self.econ_models: Dict[str, Optional[IsolationForest]] = {}
        self.phys_fitted: Dict[str, bool] = {}
        self.econ_fitted: Dict[str, bool] = {}
        self.anomalies: Dict[str, Anomaly] = {}
        self.mqtt_connected: bool = False
        self.db_connected: bool = False
        self._lock = threading.Lock()

    def get_or_create_pod(self, pod_id: str) -> None:
        with self._lock:
            if pod_id not in self.buffers:
                logger.info("New immersion pod discovered: %s", pod_id)
                self.buffers[pod_id] = deque(maxlen=BUFFER_SIZE)
                self.phys_models[pod_id] = None
                self.econ_models[pod_id] = None
                self.phys_fitted[pod_id] = False
                self.econ_fitted[pod_id] = False

    def add_reading(self, pod_id: str, reading: Dict[str, Any]) -> None:
        with self._lock:
            if pod_id not in self.buffers:
                self.buffers[pod_id] = deque(maxlen=BUFFER_SIZE)
                self.phys_models[pod_id] = None
                self.econ_models[pod_id] = None
                self.phys_fitted[pod_id] = False
                self.econ_fitted[pod_id] = False
            self.buffers[pod_id].append(reading)

    def get_readings(self, pod_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if pod_id in self.buffers:
                return list(self.buffers[pod_id])
            return []

    def register_anomaly(self, anomaly: Anomaly) -> None:
        with self._lock:
            self.anomalies[anomaly.anomaly_id] = anomaly
        logger.warning(
            "ANOMALY [%s] pod=%s metric=%s value=%.2f method=%s",
            anomaly.severity.upper(), anomaly.pod_id, anomaly.metric, anomaly.value, anomaly.method,
        )
        self._forward_to_ignition(anomaly)

    def _forward_to_ignition(self, anomaly: Anomaly) -> None:
        if not IGNITION_WEBHOOK_URL:
            return
        def _post():
            try:
                payload = json.dumps(anomaly.model_dump()).encode("utf-8")
                req = urllib.request.Request(
                    IGNITION_WEBHOOK_URL,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status < 300:
                        logger.debug("Successfully forwarded anomaly to Ignition Event Stream")
            except Exception as e:
                logger.debug("Ignition Event Stream webhook not reachable: %s", e)
        threading.Thread(target=_post, daemon=True).start()

    def acknowledge_anomaly(self, anomaly_id: str) -> bool:
        with self._lock:
            if anomaly_id in self.anomalies:
                self.anomalies[anomaly_id].acknowledged = True
                return True
            return False

    def get_active_anomalies(self) -> List[Anomaly]:
        with self._lock:
            return [a for a in self.anomalies.values() if not a.acknowledged]

    def get_pod_anomaly_ids(self, pod_id: str) -> List[str]:
        with self._lock:
            return [
                aid for aid, a in self.anomalies.items()
                if a.pod_id == pod_id and not a.acknowledged
            ]


# =============================================================================
# ENSEMBLE ANOMALY DETECTOR
# =============================================================================

class AnomalyDetector:
    def __init__(self, state: AgentState) -> None:
        self.state = state

    def check_thresholds(self, pod_id: str, reading: Dict[str, Any]) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        ts = reading["timestamp"]

        # --- Thermodynamics: HKUST-1 Critical Heat Flux ---
        heat_flux = float(reading.get("heat_flux_w_cm2", 0.0))
        if heat_flux >= THRESHOLD_CHF_CRIT:
            anomalies.append(self._make_anomaly(
                pod_id, "heat_flux_w_cm2", heat_flux, THRESHOLD_CHF_CRIT,
                "critical", "threshold", ts
            ))
        elif heat_flux >= THRESHOLD_CHF_WARN:
            anomalies.append(self._make_anomaly(
                pod_id, "heat_flux_w_cm2", heat_flux, THRESHOLD_CHF_WARN,
                "warning", "threshold", ts
            ))

        fluid_temp = float(reading.get("fluid_temp_c", 0.0))
        if fluid_temp >= THRESHOLD_FLUID_CRIT:
            anomalies.append(self._make_anomaly(
                pod_id, "fluid_temp_c", fluid_temp, THRESHOLD_FLUID_CRIT,
                "critical", "threshold", ts
            ))
        elif fluid_temp >= THRESHOLD_FLUID_WARN:
            anomalies.append(self._make_anomaly(
                pod_id, "fluid_temp_c", fluid_temp, THRESHOLD_FLUID_WARN,
                "warning", "threshold", ts
            ))

        # --- Economics: Smart Contract Payout SLA ---
        unsettled = float(reading.get("unsettled_balance_usd", 0.0))
        if unsettled >= THRESHOLD_UNSETTLED_CRIT:
            anomalies.append(self._make_anomaly(
                pod_id, "unsettled_balance_usd", unsettled, THRESHOLD_UNSETTLED_CRIT,
                "critical", "threshold", ts
            ))
        elif unsettled >= THRESHOLD_UNSETTLED_WARN:
            anomalies.append(self._make_anomaly(
                pod_id, "unsettled_balance_usd", unsettled, THRESHOLD_UNSETTLED_WARN,
                "warning", "threshold", ts
            ))

        return anomalies

    def check_isolation_forests(self, pod_id: str, reading: Dict[str, Any]) -> List[Anomaly]:
        readings = self.state.get_readings(pod_id)
        if len(readings) < IF_FIT_THRESHOLD:
            return []

        anomalies: List[Anomaly] = []

        # --- 1. Physical Isolation Forest ---
        X_phys = np.array([
            [
                r.get("heat_flux_w_cm2", 130.0),
                r.get("fluid_temp_c", 55.0),
                r.get("vapor_pressure_kpa", 101.3),
                r.get("water_harvested_liters", 5.0),
            ]
            for r in readings
        ], dtype=np.float32)

        if not self.state.phys_fitted.get(pod_id, False):
            phys_model = IsolationForest(
                n_estimators=100, contamination=IF_CONTAMINATION, random_state=42, n_jobs=1
            )
            phys_model.fit(X_phys)
            with self.state._lock:
                self.state.phys_models[pod_id] = phys_model
                self.state.phys_fitted[pod_id] = True
            logger.info("Physical Isolation Forest fitted for pod %s on %d samples", pod_id, len(readings))
        else:
            phys_model = self.state.phys_models.get(pod_id)
            if phys_model:
                x_latest_phys = np.array([[
                    reading.get("heat_flux_w_cm2", 130.0),
                    reading.get("fluid_temp_c", 55.0),
                    reading.get("vapor_pressure_kpa", 101.3),
                    reading.get("water_harvested_liters", 5.0),
                ]], dtype=np.float32)
                pred = phys_model.predict(x_latest_phys)[0]
                if pred == -1:
                    score = float(phys_model.score_samples(x_latest_phys)[0])
                    severity = "critical" if score < -0.35 else "warning"
                    anomalies.append(self._make_anomaly(
                        pod_id, "Multivariate_Thermodynamics", score, None,
                        severity, "isolation_forest_phys", reading["timestamp"]
                    ))

        # --- 2. Economic Isolation Forest ---
        X_econ = np.array([
            [
                r.get("compute_yield_usd", 100.0),
                r.get("unsettled_balance_usd", 0.0),
            ]
            for r in readings
        ], dtype=np.float32)

        if not self.state.econ_fitted.get(pod_id, False):
            econ_model = IsolationForest(
                n_estimators=100, contamination=IF_CONTAMINATION, random_state=42, n_jobs=1
            )
            econ_model.fit(X_econ)
            with self.state._lock:
                self.state.econ_models[pod_id] = econ_model
                self.state.econ_fitted[pod_id] = True
            logger.info("Economic Isolation Forest fitted for pod %s on %d samples", pod_id, len(readings))
        else:
            econ_model = self.state.econ_models.get(pod_id)
            if econ_model:
                x_latest_econ = np.array([[
                    reading.get("compute_yield_usd", 100.0),
                    reading.get("unsettled_balance_usd", 0.0),
                ]], dtype=np.float32)
                pred = econ_model.predict(x_latest_econ)[0]
                if pred == -1:
                    score = float(econ_model.score_samples(x_latest_econ)[0])
                    severity = "critical" if score < -0.35 else "warning"
                    anomalies.append(self._make_anomaly(
                        pod_id, "Multivariate_Economics", score, None,
                        severity, "isolation_forest_econ", reading["timestamp"]
                    ))

        return anomalies

    @staticmethod
    def _make_anomaly(
        pod_id: str, metric: str, value: float, threshold: Optional[float],
        severity: str, method: str, timestamp: str,
    ) -> Anomaly:
        return Anomaly(
            anomaly_id=str(uuid.uuid4()),
            pod_id=pod_id,
            metric=metric,
            value=round(value, 3),
            threshold=threshold,
            severity=severity,
            method=method,
            detected_at=timestamp,
            acknowledged=False,
        )


# =============================================================================
# SINGLETON INSTANCES & ASYNC QUEUE
# =============================================================================
app_state = AgentState()
detector = AnomalyDetector(app_state)
db_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
db_pool: Optional[asyncpg.Pool] = None


# =============================================================================
# DATABASE INGESTION WORKER
# =============================================================================

async def db_writer_worker():
    """Background worker that drains db_queue and inserts into TimescaleDB."""
    logger.info("TimescaleDB async writer worker started.")
    while True:
        try:
            item = await db_queue.get()
            if db_pool:
                try:
                    async with db_pool.acquire() as conn:
                        # Parse timestamp string to datetime object for timestamptz
                        ts_str = item.get("timestamp")
                        try:
                            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except Exception:
                            dt = datetime.now(timezone.utc)

                        await conn.execute(
                            """
                            INSERT INTO ecosystem_telemetry (
                                time, node_id, participant_id, heat_flux_w_cm2, fluid_temp_c,
                                vapor_pressure_kpa, water_harvested_liters, compute_yield_usd,
                                distributed_profit_usd, unsettled_balance_usd
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            ON CONFLICT (time, node_id) DO NOTHING;
                            """,
                            dt,
                            item.get("node_id"),
                            item.get("participant_id", "default_operator"),
                            item.get("heat_flux_w_cm2"),
                            item.get("fluid_temp_c"),
                            item.get("vapor_pressure_kpa"),
                            item.get("water_harvested_liters"),
                            item.get("compute_yield_usd"),
                            item.get("distributed_profit_usd", 0.0),
                            item.get("unsettled_balance_usd"),
                        )
                except Exception as e:
                    logger.error("Error inserting telemetry into TimescaleDB: %s", e)
            db_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Unexpected error in db_writer_worker: %s", e)
            await asyncio.sleep(1)


# =============================================================================
# MQTT CLIENT INTEGRATION
# =============================================================================

def extract_pod_id(topic: str) -> str:
    parts = topic.split("/")
    return parts[-1] if parts else "unknown_pod"


def parse_payload(payload_bytes: bytes) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(payload_bytes.decode("utf-8"))
        result: Dict[str, Any] = {}

        if "metrics" in data:
            # Sparkplug B JSON format
            for m in data.get("metrics", []):
                name = m.get("name", "")
                val = m.get("value", 0.0)
                if "HeatFlux" in name or "heat_flux" in name:
                    result["heat_flux_w_cm2"] = float(val)
                elif "FluidTemp" in name or "fluid_temp" in name:
                    result["fluid_temp_c"] = float(val)
                elif "VaporPressure" in name or "vapor_pressure" in name:
                    result["vapor_pressure_kpa"] = float(val)
                elif "Water" in name or "water" in name:
                    result["water_harvested_liters"] = float(val)
                elif "Compute" in name or "compute" in name:
                    result["compute_yield_usd"] = float(val)
                elif "Unsettled" in name or "unsettled" in name:
                    result["unsettled_balance_usd"] = float(val)
                elif "Participant" in name or "participant" in name:
                    result["participant_id"] = str(val)
        else:
            # Flat JSON format
            result = {
                "heat_flux_w_cm2": float(data.get("heat_flux_w_cm2", 130.0)),
                "fluid_temp_c": float(data.get("fluid_temp_c", 55.0)),
                "vapor_pressure_kpa": float(data.get("vapor_pressure_kpa", 101.3)),
                "water_harvested_liters": float(data.get("water_harvested_liters", 5.0)),
                "compute_yield_usd": float(data.get("compute_yield_usd", 100.0)),
                "unsettled_balance_usd": float(data.get("unsettled_balance_usd", 0.0)),
                "participant_id": str(data.get("participant_id", "default_operator")),
            }

        result.setdefault("heat_flux_w_cm2", 130.0)
        result.setdefault("fluid_temp_c", 55.0)
        result.setdefault("vapor_pressure_kpa", 101.3)
        result.setdefault("water_harvested_liters", 5.0)
        result.setdefault("compute_yield_usd", 100.0)
        result.setdefault("unsettled_balance_usd", 0.0)
        result.setdefault("participant_id", "default_operator")
        result["timestamp"] = data.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return result
    except Exception as e:
        logger.error("Failed to parse MQTT message payload: %s", e)
        return None


def make_mqtt_client() -> mqtt.Client:
    # Handle paho-mqtt 2.x and 1.x compatibility
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="Ecosystem_AI_Agent"
        )
    except AttributeError:
        client = mqtt.Client(client_id="Ecosystem_AI_Agent")

    def on_connect(c, userdata, *args):
        # args contains flags, rc, or flags, reason_code, properties depending on paho version
        rc = args[1] if len(args) > 1 and isinstance(args[1], int) else 0
        if rc == 0:
            app_state.mqtt_connected = True
            logger.info("Connected to Mosquitto MQTT Broker. Subscribing to: %s", MQTT_TOPIC)
            c.subscribe(MQTT_TOPIC, qos=1)
        else:
            logger.error("MQTT connection rejected with code %s", rc)

    def on_disconnect(c, userdata, *args):
        app_state.mqtt_connected = False
        logger.warning("Disconnected from MQTT Broker. Automatic reconnect enabled.")

    def on_message(c, userdata, msg):
        reading = parse_payload(msg.payload)
        if not reading:
            return

        pod_id = extract_pod_id(msg.topic)
        reading["node_id"] = pod_id

        # 1. Update In-Memory circular buffers
        app_state.add_reading(pod_id, reading)

        # 2. Run Deterministic and ML Anomaly Detection
        thresh_anoms = detector.check_thresholds(pod_id, reading)
        for a in thresh_anoms:
            app_state.register_anomaly(a)

        ml_anoms = detector.check_isolation_forests(pod_id, reading)
        for a in ml_anoms:
            app_state.register_anomaly(a)

        # 3. Enqueue to TimescaleDB async ingest queue
        try:
            db_queue.put_nowait(reading)
        except asyncio.QueueFull:
            logger.warning("Database ingestion queue full; dropping point to preserve real-time detection.")
        except Exception:
            pass  # Loop might not be bound if called early

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


mqtt_client = make_mqtt_client()


def mqtt_thread_target():
    logger.info("Connecting MQTT client to %s:%d ...", BROKER_HOST, BROKER_PORT)
    while True:
        try:
            mqtt_client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
            mqtt_client.loop_forever()
        except Exception as e:
            logger.error("MQTT connection failed (%s). Retrying in 5s...", e)
            import time
            time.sleep(5)


# =============================================================================
# FASTAPI APPLICATION & LIFESPAN
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("Initializing application lifespan...")

    # 1. Initialize TimescaleDB Connection Pool
    if DATABASE_URL:
        try:
            db_pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=10,
                timeout=10.0,
            )
            app_state.db_connected = True
            logger.info("Connected to TimescaleDB connection pool.")
        except Exception as e:
            logger.warning("TimescaleDB not yet reachable (%s). Proceeding in offline mode.", e)
            app_state.db_connected = False

    # 2. Launch background DB writer task
    writer_task = asyncio.create_task(db_writer_worker())

    # 3. Start MQTT background thread
    t = threading.Thread(target=mqtt_thread_target, daemon=True, name="MQTT-Subscriber")
    t.start()

    yield

    # Clean shutdown
    writer_task.cancel()
    if db_pool:
        await db_pool.close()
    try:
        mqtt_client.disconnect()
    except Exception:
        pass
    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Regenerative Data Center Anomaly Agent",
    description="Production-grade Triple-Bottom-Line anomaly detection and TimescaleDB ingestion.",
    version="2.1.0",
    lifespan=lifespan,
)


# =============================================================================
# REST ENDPOINTS (MCP TOOLS)
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for Docker container orchestration and load balancers."""
    return HealthResponse(
        status="ok",
        service="ecosystem_anomaly_agent",
        db_connected=app_state.db_connected,
        mqtt_connected=app_state.mqtt_connected,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@app.get("/status", response_model=FacilityStatus)
def get_facility_status():
    """Retrieve holistic health summary across all Immersion Pods."""
    all_anomalies = app_state.get_active_anomalies()
    pods: List[PodStatus] = []

    with app_state._lock:
        pod_ids = list(app_state.buffers.keys())

    for pod_id in pod_ids:
        readings = app_state.get_readings(pod_id)
        if not readings:
            continue
        latest = readings[-1]
        active_ids = app_state.get_pod_anomaly_ids(pod_id)

        status = "normal"
        if active_ids:
            pod_anoms = [a for a in all_anomalies if a.anomaly_id in active_ids]
            if any(a.severity == "critical" for a in pod_anoms):
                status = "critical"
            else:
                status = "warning"

        pods.append(PodStatus(
            pod_id=pod_id,
            latest_metrics=EcosystemTelemetry(**latest),
            sample_count=len(readings),
            phys_model_fitted=app_state.phys_fitted.get(pod_id, False),
            econ_model_fitted=app_state.econ_fitted.get(pod_id, False),
            active_anomalies=active_ids,
            status=status,
        ))

    overall = "normal"
    if any(p.status == "critical" for p in pods):
        overall = "critical"
    elif any(p.status == "warning" for p in pods):
        overall = "warning"

    return FacilityStatus(
        facility="Alpha-Regen-DataCenter-01",
        pod_count=len(pods),
        active_anomaly_count=len(all_anomalies),
        pods=pods,
        overall_status=overall,
        last_updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


@app.get("/anomalies", response_model=List[Anomaly])
def get_anomalies(severity: Optional[str] = Query(None, description="'warning' or 'critical'")):
    """List all active, unacknowledged anomalies affecting the facility."""
    anoms = app_state.get_active_anomalies()
    if severity:
        anoms = [a for a in anoms if a.severity.lower() == severity.lower()]
    return anoms


@app.get("/pod/{pod_id}", response_model=PodStatus)
def get_pod_detail(pod_id: str):
    """Retrieve detailed state for a specific Immersion Pod."""
    readings = app_state.get_readings(pod_id)
    if not readings:
        raise HTTPException(status_code=404, detail=f"Pod '{pod_id}' not found or has sent no telemetry.")

    latest = readings[-1]
    active_ids = app_state.get_pod_anomaly_ids(pod_id)
    all_anomalies = app_state.get_active_anomalies()

    status = "normal"
    if active_ids:
        pod_anoms = [a for a in all_anomalies if a.anomaly_id in active_ids]
        if any(a.severity == "critical" for a in pod_anoms):
            status = "critical"
        else:
            status = "warning"

    return PodStatus(
        pod_id=pod_id,
        latest_metrics=EcosystemTelemetry(**latest),
        sample_count=len(readings),
        phys_model_fitted=app_state.phys_fitted.get(pod_id, False),
        econ_model_fitted=app_state.econ_fitted.get(pod_id, False),
        active_anomalies=active_ids,
        status=status,
    )


@app.post("/acknowledge/{anomaly_id}", response_model=AcknowledgeResponse)
def acknowledge_anomaly(anomaly_id: str):
    """Operator acknowledges an anomaly alert."""
    success = app_state.acknowledge_anomaly(anomaly_id)
    if not success:
        raise HTTPException(status_code=404, detail="Anomaly ID not found or already acknowledged.")

    return AcknowledgeResponse(
        anomaly_id=anomaly_id,
        acknowledged=True,
        message=f"Anomaly {anomaly_id} successfully acknowledged.",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ai_anomaly_agent:app", host="0.0.0.0", port=8000, reload=False)
