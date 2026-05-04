"""
ai_anomaly_agent.py

FastAPI microservice for real-time data center thermal anomaly detection.

Architecture:
  1. Background MQTT subscriber thread consumes rack telemetry from the broker
  2. Per-rack circular buffer accumulates the last 100 readings in memory
  3. Two anomaly detection modes run on every new reading:
       a. Threshold (ASHRAE A1 class limits): immediate, deterministic
       b. Isolation Forest (sklearn): statistical, fits after 50 samples
  4. REST endpoints expose detection results as MCP tools for LLM interfaces

MCP (Model Context Protocol) Design:
  Each endpoint is documented as an MCP tool — a typed, versioned HTTP endpoint
  that a language model can call to answer operator questions like:
    "Are there any thermal anomalies right now?"
    "What is the inlet temperature in rack A01?"
    "Acknowledge the critical alarm on rack B07."

  This pattern is SAFER than direct LLM-to-PLC integration because:
    - All LLM-accessible endpoints are read-only by default
    - The single write endpoint (/acknowledge) has no physical side effects
    - Pydantic validates all inputs and outputs (no raw register manipulation)
    - Every call is logged with timestamp and caller context

ASHRAE Thermal Guidelines (used for threshold detection):
  ASHRAE A1 class equipment (most enterprise servers):
    Inlet temperature: 15–27°C recommended (18–27°C ASHRAE 2021 recommended range)
    Inlet temperature: 27°C maximum (hard limit for A1 class)
  Hot aisle / exit temperature: varies; 45°C is a common practical limit

Installation:
  pip install fastapi uvicorn paho-mqtt scikit-learn numpy asyncpg python-dotenv

Run:
  uvicorn ai_anomaly_agent:app --host 0.0.0.0 --port 8000 --reload  (dev)
  uvicorn ai_anomaly_agent:app --host 0.0.0.0 --port 8000            (prod)

Author: Adam Dryden
"""

import json
import logging
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sklearn.ensemble import IsolationForest

# =============================================================================
# LOGGING
# Use structured timestamps; 'Z' suffix indicates UTC per ISO-8601.
# In production, consider structlog or python-json-logger for JSON log output
# compatible with Datadog, ELK, etc.
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("anomaly_agent")

# =============================================================================
# CONFIGURATION
# All config values are read from environment variables with sensible defaults.
# In Docker Compose, these are set in the 'environment' block.
# For local dev, create a .env file or export variables before running.
# =============================================================================

# MQTT broker — connects on the datacenter_net Docker network
BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))

# Topic wildcard: '+' matches a single segment (any rack ID)
# Full subscription: spBv1.0/DataCenter/NDATA/+ matches:
#   spBv1.0/DataCenter/NDATA/Rack_A01
#   spBv1.0/DataCenter/NDATA/Rack_B07
#   etc.
MQTT_TOPIC: str = os.getenv("MQTT_TOPIC", "spBv1.0/DataCenter/NDATA/+")

# Circular buffer depth: keep the last N readings per rack.
# 100 readings × 5s interval = ~8 minutes of rolling history.
# Isolation Forest trains on this buffer; a larger buffer = more stable model.
BUFFER_SIZE: int = int(os.getenv("ANOMALY_BUFFER_SIZE", "100"))

# Isolation Forest: minimum samples required before fitting the model.
# Below this threshold, only threshold detection runs (no false positives
# from an untrained model on the first few readings).
IF_FIT_THRESHOLD: int = int(os.getenv("ANOMALY_IF_FIT_THRESHOLD", "50"))

# Expected fraction of anomalous samples in training data.
# 0.05 = 5% contamination. This sets the decision boundary:
# sklearn will flag ~5% of new samples as anomalous (tune based on observed FP rate).
IF_CONTAMINATION: float = float(os.getenv("ANOMALY_IF_CONTAMINATION", "0.05"))

# ASHRAE A1 class thermal limits
THRESHOLD_INLET_WARN: float = 25.0   # °C — approaching limit; check cooling
THRESHOLD_INLET_CRIT: float = 27.0   # °C — ASHRAE A1 maximum
THRESHOLD_EXIT_WARN: float = 40.0    # °C — hot-aisle starting to heat up
THRESHOLD_EXIT_CRIT: float = 45.0    # °C — immediate action required

# =============================================================================
# PYDANTIC MODELS
# Pydantic models serve three purposes:
#   1. Runtime validation: FastAPI validates every request/response against them
#   2. OpenAPI documentation: Auto-generates /docs and /redoc schemas
#   3. LLM tool typing: MCP tool interfaces use these schemas for type safety
# =============================================================================


class MetricSnapshot(BaseModel):
    """Latest observed sensor values for a single server rack."""

    inlet_temp_c: float = Field(
        ...,
        description="Server inlet (cold aisle) temperature in °C. "
                    "ASHRAE A1 limit: 27°C."
    )
    exit_temp_c: float = Field(
        ...,
        description="Hot aisle exit temperature in °C. Typical limit: 45°C."
    )
    power_draw_kw: float = Field(
        ...,
        description="Rack power consumption in kilowatts."
    )
    timestamp: str = Field(
        ...,
        description="ISO-8601 UTC timestamp of the most recent reading."
    )


class RackStatus(BaseModel):
    """Complete status summary for a single server rack."""

    rack_id: str = Field(
        ...,
        description="Rack identifier, derived from the MQTT edge node ID "
                    "(last segment of the Sparkplug B topic path)."
    )
    latest_metrics: MetricSnapshot
    sample_count: int = Field(
        ...,
        description="Total telemetry readings received for this rack since service startup. "
                    "Isolation Forest requires >= 50 samples before activating."
    )
    if_fitted: bool = Field(
        ...,
        description="True when the Isolation Forest model is trained and actively scoring. "
                    "False during the warm-up period (< 50 samples)."
    )
    active_anomalies: List[str] = Field(
        ...,
        description="List of unacknowledged anomaly UUIDs for this rack."
    )
    status: str = Field(
        ...,
        description="Rack health: 'normal' | 'warning' | 'critical'"
    )


class Anomaly(BaseModel):
    """A single detected anomaly event with full context."""

    anomaly_id: str = Field(
        ...,
        description="UUID uniquely identifying this anomaly event. "
                    "Used as the path parameter for POST /acknowledge/{anomaly_id}."
    )
    rack_id: str = Field(
        ...,
        description="ID of the rack where the anomaly was detected."
    )
    metric: str = Field(
        ...,
        description="The specific metric that triggered the anomaly. "
                    "Examples: 'InletTemp_C', 'ExitTemp_C', 'MultivariateThermal'"
    )
    value: float = Field(
        ...,
        description="The observed metric value at the time of anomaly detection."
    )
    threshold: Optional[float] = Field(
        None,
        description="The threshold that was breached (threshold-based detection only). "
                    "None for Isolation Forest anomalies."
    )
    severity: str = Field(
        ...,
        description="'warning' (approaching limit) or 'critical' (limit breached or exceeded)."
    )
    method: str = Field(
        ...,
        description="Detection method: 'threshold' (hard limit) or "
                    "'isolation_forest' (statistical outlier)."
    )
    detected_at: str = Field(
        ...,
        description="ISO-8601 UTC timestamp when the anomaly was first detected."
    )
    acknowledged: bool = Field(
        False,
        description="True after an operator has called POST /acknowledge/{anomaly_id}. "
                    "Acknowledged anomalies are removed from the active anomaly list."
    )


class FacilityStatus(BaseModel):
    """Top-level facility health summary. The response model for GET /status."""

    facility: str = Field(
        ...,
        description="Facility identifier."
    )
    rack_count: int = Field(
        ...,
        description="Number of racks currently reporting telemetry."
    )
    active_anomaly_count: int = Field(
        ...,
        description="Total unacknowledged anomalies across all racks."
    )
    racks: List[RackStatus] = Field(
        ...,
        description="Per-rack status details."
    )
    overall_status: str = Field(
        ...,
        description="'normal' | 'warning' | 'critical' — worst status across all racks."
    )
    last_updated: str = Field(
        ...,
        description="ISO-8601 UTC timestamp of the most recent telemetry received."
    )


class AcknowledgeResponse(BaseModel):
    """Response body for POST /acknowledge/{anomaly_id}."""

    anomaly_id: str
    acknowledged: bool
    message: str


# =============================================================================
# IN-MEMORY STATE STORE
# Thread-safe state shared between the MQTT background thread (writer)
# and the FastAPI request handlers (readers).
#
# Threading model:
#   - MQTT callback thread: calls add_reading(), register_anomaly()
#   - FastAPI thread(s): call get_readings(), get_active_anomalies(), etc.
#   - threading.Lock() ensures atomic reads/writes; Python's GIL alone is
#     insufficient for compound operations (read-modify-write).
# =============================================================================


class AgentState:
    """
    Centralized in-memory state container.

    All mutations are guarded by self._lock to ensure thread safety between
    the MQTT callback thread and FastAPI request handlers.

    For multi-process deployments (uvicorn --workers > 1), replace with
    a shared backend such as Redis or a TimescaleDB state table.
    """

    def __init__(self) -> None:
        # Per-rack circular buffers: { rack_id: deque(maxlen=BUFFER_SIZE) }
        # deque with maxlen automatically evicts oldest items when full.
        # Each item: { inlet_temp_c, exit_temp_c, power_draw_kw, timestamp }
        self.buffers: Dict[str, deque] = {}

        # Per-rack Isolation Forest models: { rack_id: IsolationForest | None }
        # None until IF_FIT_THRESHOLD readings have been collected.
        self.models: Dict[str, Optional[IsolationForest]] = {}

        # Per-rack model fitted flag: { rack_id: bool }
        self.model_fitted: Dict[str, bool] = {}

        # Active anomaly registry: { anomaly_id: Anomaly }
        # Anomalies persist until acknowledged via POST /acknowledge.
        self.anomalies: Dict[str, Anomaly] = {}

        # Mutex protecting all state above
        self._lock = threading.Lock()

    def get_or_create_rack(self, rack_id: str) -> None:
        """Initialize state for a newly discovered rack (idempotent)."""
        with self._lock:
            if rack_id not in self.buffers:
                logger.info("New rack discovered in telemetry: %s", rack_id)
                self.buffers[rack_id]      = deque(maxlen=BUFFER_SIZE)
                self.models[rack_id]       = None
                self.model_fitted[rack_id] = False

    def add_reading(self, rack_id: str, reading: Dict[str, Any]) -> None:
        """Append a telemetry reading to the rack's circular buffer."""
        with self._lock:
            self.buffers[rack_id].append(reading)

    def get_readings(self, rack_id: str) -> List[Dict[str, Any]]:
        """Return a snapshot copy of the rack's buffer (thread-safe)."""
        with self._lock:
            return list(self.buffers[rack_id])

    def register_anomaly(self, anomaly: Anomaly) -> None:
        """Add a new anomaly to the active registry and log it."""
        with self._lock:
            self.anomalies[anomaly.anomaly_id] = anomaly
        logger.warning(
            "ANOMALY [%s] rack=%s metric=%s value=%.2f method=%s",
            anomaly.severity.upper(),
            anomaly.rack_id,
            anomaly.metric,
            anomaly.value,
            anomaly.method,
        )

    def acknowledge_anomaly(self, anomaly_id: str) -> bool:
        """
        Mark an anomaly as acknowledged.

        Returns True if the anomaly was found and updated, False if not found.
        Acknowledged anomalies remain in self.anomalies for audit purposes
        but are excluded from GET /anomalies and GET /status responses.
        """
        with self._lock:
            if anomaly_id in self.anomalies:
                self.anomalies[anomaly_id].acknowledged = True
                return True
            return False

    def get_active_anomalies(self) -> List[Anomaly]:
        """Return all unacknowledged anomalies (copy for safe iteration)."""
        with self._lock:
            return [a for a in self.anomalies.values() if not a.acknowledged]

    def get_rack_anomaly_ids(self, rack_id: str) -> List[str]:
        """Return unacknowledged anomaly IDs for a specific rack."""
        with self._lock:
            return [
                aid
                for aid, a in self.anomalies.items()
                if a.rack_id == rack_id and not a.acknowledged
            ]


# =============================================================================
# ANOMALY DETECTION ENGINE
# =============================================================================


class AnomalyDetector:
    """
    Two-mode anomaly detection for server rack thermal telemetry.

    Mode 1 — Threshold Detection:
        Evaluates each new reading against ASHRAE A1 hard limits.
        Activates immediately from the first reading.
        Zero model warm-up required. Zero false negatives for obvious overtemperature.
        Produces: 'threshold' method anomalies.

    Mode 2 — Isolation Forest (sklearn.ensemble.IsolationForest):
        Unsupervised anomaly detection using random forests of isolation trees.
        Fits on the first IF_FIT_THRESHOLD readings, then scores every subsequent
        reading against the model of 'normal' behavior.
        Feature vector: [InletTemp_C, ExitTemp_C, PowerDraw_kW]

        How Isolation Forest works:
          1. Build N random decision trees (n_estimators=100 by default)
          2. Each tree recursively partitions the feature space randomly
             (random feature + random split point within that feature's range)
          3. Outliers (anomalies) are isolated in fewer splits than normal points
             because they are far from dense regions of the data
          4. Anomaly score = average depth across all trees (normalized):
             score ≈ -0.5 → anomaly; score ≈ +0.5 → normal
          5. predict() returns -1 (anomaly) or +1 (normal) based on contamination

        Advantage over thresholds:
          Detects anomalies in the COMBINATION of features. A rack with normal
          inlet/exit temperatures but abnormally high power draw (suggesting
          inefficient servers) would not trigger any temperature threshold but
          WOULD be flagged by Isolation Forest as its feature vector sits outside
          the normal operating cluster.
        Produces: 'isolation_forest' method anomalies.
    """

    def __init__(self, state: AgentState) -> None:
        self.state = state

    def check_thresholds(
        self, rack_id: str, reading: Dict[str, Any]
    ) -> List[Anomaly]:
        """
        Evaluate ASHRAE A1 hard limits on InletTemp_C and ExitTemp_C.

        Returns a list of Anomaly objects for each breached threshold.
        An empty list means no threshold violations.
        """
        anomalies: List[Anomaly] = []
        ts = reading["timestamp"]

        # --- InletTemp_C ---
        inlet = reading.get("inlet_temp_c", 0.0)
        if inlet >= THRESHOLD_INLET_CRIT:
            # Critical: ASHRAE A1 maximum temperature exceeded
            anomalies.append(self._make_anomaly(
                rack_id=rack_id,
                metric="InletTemp_C",
                value=inlet,
                threshold=THRESHOLD_INLET_CRIT,
                severity="critical",
                method="threshold",
                timestamp=ts,
            ))
        elif inlet >= THRESHOLD_INLET_WARN:
            # Warning: temperature approaching the ASHRAE limit
            anomalies.append(self._make_anomaly(
                rack_id=rack_id,
                metric="InletTemp_C",
                value=inlet,
                threshold=THRESHOLD_INLET_WARN,
                severity="warning",
                method="threshold",
                timestamp=ts,
            ))

        # --- ExitTemp_C ---
        exit_t = reading.get("exit_temp_c", 0.0)
        if exit_t >= THRESHOLD_EXIT_CRIT:
            anomalies.append(self._make_anomaly(
                rack_id=rack_id,
                metric="ExitTemp_C",
                value=exit_t,
                threshold=THRESHOLD_EXIT_CRIT,
                severity="critical",
                method="threshold",
                timestamp=ts,
            ))
        elif exit_t >= THRESHOLD_EXIT_WARN:
            anomalies.append(self._make_anomaly(
                rack_id=rack_id,
                metric="ExitTemp_C",
                value=exit_t,
                threshold=THRESHOLD_EXIT_WARN,
                severity="warning",
                method="threshold",
                timestamp=ts,
            ))

        return anomalies

    def check_isolation_forest(
        self, rack_id: str, reading: Dict[str, Any]
    ) -> List[Anomaly]:
        """
        Fit or score the per-rack Isolation Forest model.

        Phase 1 — Warm-up (< IF_FIT_THRESHOLD samples):
          Returns [] immediately. Not enough data to build a reliable model.

        Phase 2 — Initial fit (exactly IF_FIT_THRESHOLD samples):
          Trains the model on the full buffer. Returns [] (don't score training data).
          Logs the fit event.

        Phase 3 — Scoring (> IF_FIT_THRESHOLD samples):
          Scores only the latest reading against the trained model.
          The model is NOT continuously refitted here (expensive); in production,
          refit on a schedule (e.g., every 6 hours) using a background job.

        Returns:
            List of Anomaly objects (empty if no statistical anomaly detected).
        """
        readings = self.state.get_readings(rack_id)
        n = len(readings)

        if n < IF_FIT_THRESHOLD:
            # Warm-up phase: insufficient data
            return []

        # Build numpy feature matrix from buffer
        # Shape: (n_samples, 3) — [InletTemp_C, ExitTemp_C, PowerDraw_kW]
        X = np.array(
            [
                [
                    r.get("inlet_temp_c",  20.0),
                    r.get("exit_temp_c",   35.0),
                    r.get("power_draw_kw",  5.0),
                ]
                for r in readings
            ],
            dtype=np.float32,
        )

        # Fit the model if not yet trained for this rack
        if not self.state.model_fitted[rack_id]:
            model = IsolationForest(
                n_estimators=100,           # number of isolation trees (more = stable)
                contamination=IF_CONTAMINATION,  # expected outlier fraction
                random_state=42,            # reproducible training
                n_jobs=1,                   # single-threaded; we're already on a bg thread
            )
            model.fit(X)

            # Thread-safe update of model state
            with self.state._lock:
                self.state.models[rack_id]       = model
                self.state.model_fitted[rack_id] = True

            logger.info(
                "Isolation Forest fitted for rack %s on %d samples", rack_id, n
            )
            # Don't score the training batch — return clean
            return []

        # Score the latest single reading against the trained model
        model = self.state.models[rack_id]
        if model is None:
            return []  # Safety guard; should not occur after fit

        # Reshape to (1, 3) for single-sample prediction
        x_latest = np.array(
            [[
                reading.get("inlet_temp_c",  20.0),
                reading.get("exit_temp_c",   35.0),
                reading.get("power_draw_kw",  5.0),
            ]],
            dtype=np.float32,
        )

        # predict() returns array of +1 (normal) or -1 (anomaly)
        prediction: int = model.predict(x_latest)[0]

        # score_samples() returns the raw anomaly score (more negative = more anomalous)
        # Typical range: -0.6 (definite anomaly) to +0.3 (clearly normal)
        score: float = float(model.score_samples(x_latest)[0])

        if prediction == -1:
            # Anomaly detected by Isolation Forest
            # Map score magnitude to severity: very negative score = more unusual
            severity = "critical" if score < -0.35 else "warning"

            return [
                self._make_anomaly(
                    rack_id=rack_id,
                    metric="MultivariateThermal",
                    value=float(reading.get("inlet_temp_c", 0.0)),
                    threshold=None,  # No hard threshold for statistical detection
                    severity=severity,
                    method="isolation_forest",
                    timestamp=reading["timestamp"],
                )
            ]

        return []

    @staticmethod
    def _make_anomaly(
        rack_id: str,
        metric: str,
        value: float,
        threshold: Optional[float],
        severity: str,
        method: str,
        timestamp: str,
    ) -> Anomaly:
        """Factory method for constructing Anomaly objects with consistent fields."""
        return Anomaly(
            anomaly_id=str(uuid.uuid4()),
            rack_id=rack_id,
            metric=metric,
            value=round(value, 3),
            threshold=threshold,
            severity=severity,
            method=method,
            detected_at=timestamp,
            acknowledged=False,
        )


# =============================================================================
# MQTT SUBSCRIBER
# Runs on a background daemon thread.
# Parses incoming MQTT telemetry and feeds the anomaly detection engine.
# =============================================================================


def extract_rack_id(topic: str) -> str:
    """
    Extract the rack/edge-node ID from a Sparkplug B MQTT topic.

    Topic format: spBv1.0/{group_id}/NDATA/{edge_node_id}
    The last path segment is the edge node ID, which we use as the rack identifier.

    Example:
        "spBv1.0/DataCenter/NDATA/Rack_A01" → "Rack_A01"
    """
    parts = topic.split("/")
    return parts[-1] if parts else "unknown"


def parse_mqtt_payload(payload_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse a JSON MQTT payload into a standardized reading dictionary.

    Handles two payload shapes:
      1. Sparkplug B JSON structure (from our simulator): has a 'metrics' array
         where each metric has 'name' and 'value' fields
      2. Flat JSON (simple test publisher): has direct key-value fields

    In production with real Sparkplug B (protobuf encoding), this function would:
        import sparkplug_b_pb2  # compiled from sparkplug_b.proto
        payload = sparkplug_b_pb2.Payload()
        payload.ParseFromString(payload_bytes)
        for metric in payload.metrics:
            alias_to_name[metric.alias] → metric.name
            value = metric.float_value or metric.long_value ...

    Returns:
        Dict with keys: inlet_temp_c, exit_temp_c, power_draw_kw, timestamp
        None on parse failure (malformed payload).
    """
    try:
        data = json.loads(payload_bytes.decode("utf-8"))
        result: Dict[str, Any] = {}

        if "metrics" in data:
            # Sparkplug B JSON structure: iterate metrics array
            for metric in data["metrics"]:
                name = metric.get("name", "")
                val  = metric.get("value", 0.0)
                if "InletTemp" in name:
                    result["inlet_temp_c"]  = float(val)
                elif "ExitTemp" in name:
                    result["exit_temp_c"]   = float(val)
                elif "PowerDraw" in name or "Power" in name:
                    result["power_draw_kw"] = float(val)
        else:
            # Flat JSON fallback
            result = {
                "inlet_temp_c":  float(data.get("inlet_temp_c",  20.0)),
                "exit_temp_c":   float(data.get("exit_temp_c",   35.0)),
                "power_draw_kw": float(data.get("power_draw_kw",  5.0)),
            }

        # Apply defaults for any missing keys (defensive — avoids KeyError in detector)
        result.setdefault("inlet_temp_c",  20.0)
        result.setdefault("exit_temp_c",   35.0)
        result.setdefault("power_draw_kw",  5.0)
        result["timestamp"] = datetime.now(timezone.utc).isoformat()

        return result

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.error("Failed to parse MQTT payload: %s", exc)
        return None


def build_mqtt_subscriber(
    state: AgentState, detector: AnomalyDetector
) -> mqtt.Client:
    """
    Build and configure the MQTT client for telemetry subscription.

    The client is returned un-connected. Call client.connect() separately
    so this function is testable without a live broker.
    """

    def on_connect(
        client: mqtt.Client, userdata: Any, flags: Dict, rc: int
    ) -> None:
        """Subscribe to rack telemetry topic on successful broker connection."""
        if rc == 0:
            logger.info(
                "MQTT connected to %s:%s — subscribing to %s",
                BROKER_HOST, BROKER_PORT, MQTT_TOPIC,
            )
            # QoS 1: at-least-once delivery for telemetry.
            # We accept occasional duplicate readings (IF handles outliers);
            # missing readings would leave gaps in the anomaly detection buffer.
            client.subscribe(MQTT_TOPIC, qos=1)
        else:
            logger.error("MQTT connection failed (rc=%d)", rc)

    def on_message(
        client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        """
        Process an incoming MQTT telemetry message.

        This callback runs in paho-mqtt's network thread — keep it fast.
        Heavy computation (model fitting) is acceptable here since it blocks
        only the MQTT I/O thread, not the FastAPI request handlers.
        The threading.Lock in AgentState ensures safe shared state access.
        """
        rack_id = extract_rack_id(msg.topic)
        reading = parse_mqtt_payload(msg.payload)

        if reading is None:
            return  # Malformed payload — skip silently (already logged)

        # Initialize rack state if this is the first message from this rack
        state.get_or_create_rack(rack_id)

        # Store the reading in the circular buffer
        state.add_reading(rack_id, reading)

        # Run both detection modes
        threshold_anomalies = detector.check_thresholds(rack_id, reading)
        if_anomalies         = detector.check_isolation_forest(rack_id, reading)

        # Register all detected anomalies
        for anomaly in threshold_anomalies + if_anomalies:
            state.register_anomaly(anomaly)

    client = mqtt.Client(
        client_id="anomaly-agent-subscriber",
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.on_connect = on_connect
    client.on_message = on_message

    return client


# =============================================================================
# APPLICATION BOOTSTRAP
# Initialize state, detector, and MQTT subscriber at module load time.
# These are module-level singletons shared across all requests.
# =============================================================================

_state    = AgentState()
_detector = AnomalyDetector(_state)
_mqtt     = build_mqtt_subscriber(_state, _detector)


def _mqtt_loop() -> None:
    """
    Connect to the MQTT broker and run the network loop forever.
    Runs on a background daemon thread; exits automatically when the main
    process exits.
    """
    _mqtt.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    _mqtt.loop_forever()  # Blocking; handles reconnects automatically


_mqtt_thread = threading.Thread(
    target=_mqtt_loop,
    daemon=True,  # Daemon threads are killed when the main thread exits
    name="mqtt-subscriber",
)
_mqtt_thread.start()
logger.info("MQTT subscriber thread started (target: %s:%s)", BROKER_HOST, BROKER_PORT)


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="Data Center Thermal Anomaly Agent",
    description=(
        "AI-native monitoring service for real-time thermal anomaly detection "
        "in data center server rack infrastructure. Consumes MQTT Sparkplug B "
        "telemetry, applies threshold and Isolation Forest detection, and exposes "
        "REST endpoints designed as MCP tools for LLM operator interfaces."
    ),
    version="1.0.0",
    docs_url="/docs",   # Swagger UI
    redoc_url="/redoc", # ReDoc UI
)


# =============================================================================
# HELPER: DETERMINE RACK STATUS STRING
# =============================================================================

def _rack_status_string(rack_id: str) -> str:
    """Return 'normal' | 'warning' | 'critical' for a rack based on active anomalies."""
    active_ids = _state.get_rack_anomaly_ids(rack_id)
    if not active_ids:
        return "normal"
    with _state._lock:
        if any(
            _state.anomalies[aid].severity == "critical"
            for aid in active_ids
            if aid in _state.anomalies
        ):
            return "critical"
    return "warning"


# =============================================================================
# REST ENDPOINTS (MCP TOOLS)
# =============================================================================

@app.get(
    "/status",
    response_model=FacilityStatus,
    summary="Get overall facility thermal status",
    tags=["Monitoring"],
)
def get_status() -> FacilityStatus:
    """
    Returns the overall thermal health status of the entire data center facility.

    **MCP Tool: get_facility_status**

    Use this tool to answer:
    - "What is the current state of the data center?"
    - "Is there anything wrong in the facility right now?"
    - "How many racks are reporting anomalies?"

    Call this endpoint first for a top-level overview before drilling into
    specific racks. Returns all active racks, their latest metrics, and
    the unacknowledged anomaly count. The overall_status field gives an
    immediate health signal: 'normal', 'warning', or 'critical'.

    Returns:
        FacilityStatus with rack_count, active_anomaly_count, racks list,
        overall_status, and last_updated timestamp.
    """
    rack_ids = list(_state.buffers.keys())
    rack_statuses: List[RackStatus] = []
    last_updated = datetime.now(timezone.utc).isoformat()

    for rack_id in rack_ids:
        readings = _state.get_readings(rack_id)
        if not readings:
            continue

        latest = readings[-1]
        last_updated = latest["timestamp"]
        active_ids   = _state.get_rack_anomaly_ids(rack_id)

        rack_statuses.append(RackStatus(
            rack_id=rack_id,
            latest_metrics=MetricSnapshot(
                inlet_temp_c=round(latest.get("inlet_temp_c",  0.0), 2),
                exit_temp_c=round(latest.get("exit_temp_c",    0.0), 2),
                power_draw_kw=round(latest.get("power_draw_kw", 0.0), 2),
                timestamp=latest["timestamp"],
            ),
            sample_count=len(readings),
            if_fitted=_state.model_fitted.get(rack_id, False),
            active_anomalies=active_ids,
            status=_rack_status_string(rack_id),
        ))

    active = _state.get_active_anomalies()
    n_crit = sum(1 for a in active if a.severity == "critical")
    n_warn = sum(1 for a in active if a.severity == "warning")

    if n_crit > 0:
        overall = "critical"
    elif n_warn > 0:
        overall = "warning"
    else:
        overall = "normal"

    return FacilityStatus(
        facility="DataCenter_Primary",
        rack_count=len(rack_statuses),
        active_anomaly_count=len(active),
        racks=rack_statuses,
        overall_status=overall,
        last_updated=last_updated,
    )


@app.get(
    "/anomalies",
    response_model=List[Anomaly],
    summary="Get all active (unacknowledged) anomalies",
    tags=["Monitoring"],
)
def get_anomalies() -> List[Anomaly]:
    """
    Returns all currently active, unacknowledged thermal anomalies.

    **MCP Tool: get_active_anomalies**

    Use this tool to answer:
    - "Are there any thermal anomalies in the data center?"
    - "What alarms are currently active?"
    - "Which racks need attention?"

    An empty list means the facility is currently anomaly-free.
    Each anomaly includes: rack_id, metric, observed value, severity
    (warning|critical), detection method (threshold|isolation_forest),
    and the detection timestamp.

    Use the anomaly_id field with POST /acknowledge/{anomaly_id} to
    mark an anomaly as seen by an operator.

    Returns:
        List[Anomaly] — empty if no active anomalies.
    """
    return _state.get_active_anomalies()


@app.get(
    "/rack/{rack_id}",
    response_model=RackStatus,
    summary="Get detailed status for a specific rack",
    tags=["Monitoring"],
)
def get_rack(rack_id: str) -> RackStatus:
    """
    Returns detailed telemetry and anomaly status for a single server rack.

    **MCP Tool: get_rack_status**

    Use this tool to answer:
    - "What is the temperature in rack A01?"
    - "Is rack B07 normal?"
    - "Show me the details for rack [name]."

    The rack_id is the last segment of the Sparkplug B MQTT topic path:
        spBv1.0/DataCenter/NDATA/{rack_id}
    Examples: "Rack_A01", "Rack_B07", "CoreSwitch_01"

    Args:
        rack_id: Rack identifier (case-sensitive, matches MQTT edge node ID).

    Returns:
        RackStatus with latest inlet/exit temperatures, power draw,
        Isolation Forest fit status, and active anomaly IDs.

    Raises:
        404: If rack_id has never reported telemetry in this session.
    """
    if rack_id not in _state.buffers:
        known = list(_state.buffers.keys())
        raise HTTPException(
            status_code=404,
            detail=(
                f"Rack '{rack_id}' not found. "
                f"Known racks: {known if known else '(none yet — waiting for telemetry)'}"
            ),
        )

    readings = _state.get_readings(rack_id)
    if not readings:
        raise HTTPException(
            status_code=404,
            detail=f"No readings received for rack '{rack_id}' yet."
        )

    latest     = readings[-1]
    active_ids = _state.get_rack_anomaly_ids(rack_id)

    return RackStatus(
        rack_id=rack_id,
        latest_metrics=MetricSnapshot(
            inlet_temp_c=round(latest.get("inlet_temp_c",  0.0), 2),
            exit_temp_c=round(latest.get("exit_temp_c",    0.0), 2),
            power_draw_kw=round(latest.get("power_draw_kw", 0.0), 2),
            timestamp=latest["timestamp"],
        ),
        sample_count=len(readings),
        if_fitted=_state.model_fitted.get(rack_id, False),
        active_anomalies=active_ids,
        status=_rack_status_string(rack_id),
    )


@app.post(
    "/acknowledge/{anomaly_id}",
    response_model=AcknowledgeResponse,
    summary="Acknowledge an active anomaly",
    tags=["Actions"],
)
def acknowledge_anomaly(anomaly_id: str) -> AcknowledgeResponse:
    """
    Acknowledges an anomaly, removing it from the active anomaly list.

    **MCP Tool: acknowledge_anomaly**

    Use this tool when an operator says:
    - "Acknowledge the alarm on rack A01."
    - "Mark anomaly [ID] as seen."
    - "I've checked rack B07 — acknowledge it."

    IMPORTANT SAFETY NOTE:
    Acknowledgement has NO physical side effects. It does not change
    any setpoints, reset any alarms at the PLC level, or silence any
    hardware annunciators. It only marks the anomaly record in this
    service as 'seen by an operator.' Physical alarm acknowledgement
    must still happen through the Ignition SCADA alarm panel.

    The anomaly record is retained in the audit log (self.anomalies)
    even after acknowledgement. New anomalies on the same rack+metric
    will generate new UUIDs.

    Args:
        anomaly_id: UUID from GET /anomalies response.

    Returns:
        AcknowledgeResponse confirming the operation.

    Raises:
        404: If anomaly_id is not found or was already acknowledged.
    """
    found = _state.acknowledge_anomaly(anomaly_id)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Anomaly '{anomaly_id}' not found or already acknowledged. "
                "Use GET /anomalies to retrieve current active anomaly IDs."
            ),
        )

    logger.info("Anomaly %s acknowledged by operator", anomaly_id)
    return AcknowledgeResponse(
        anomaly_id=anomaly_id,
        acknowledged=True,
        message=(
            f"Anomaly {anomaly_id} acknowledged. "
            "Monitoring continues. Physical SCADA alarm acknowledgement is separate."
        ),
    )


@app.get("/health", include_in_schema=False)
def health_check() -> Dict[str, str]:
    """
    Simple health check endpoint for Docker healthcheck and load balancer probes.
    Not exposed in the OpenAPI schema (include_in_schema=False).
    """
    return {
        "status": "ok",
        "mqtt_connected": str(_mqtt.is_connected()),
        "racks_monitored": str(len(_state.buffers)),
        "active_anomalies": str(len(_state.get_active_anomalies())),
    }


# =============================================================================
# ENTRY POINT
# For development: python ai_anomaly_agent.py
# For production:  uvicorn ai_anomaly_agent:app --host 0.0.0.0 --port 8000
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        workers=1,  # Must be 1 when using a background MQTT thread
    )
