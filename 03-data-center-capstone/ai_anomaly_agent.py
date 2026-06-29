"""
ai_anomaly_agent.py

FastAPI microservice for real-time Triple-Bottom-Line ecosystem anomaly detection.

REFINEMENT V2 - ENSEMBLE DETECTION:
  Mixing thermodynamics (continuous, sub-second physics) with economics (discrete, 
  asynchronous block-time settlements) into a single machine learning model ruins 
  the clustering geometry of the Isolation Forest.
  
  We now run an Ensemble Model:
    1. Physical AI: Monitors CHF, pressure, and MOF-303 water yield.
    2. Economic AI: Monitors compute yield vs. unsettled wallet balances.

Installation:
  pip install fastapi uvicorn paho-mqtt scikit-learn numpy asyncpg python-dotenv
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
# LOGGING & CONFIG
# =============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-8s] %(message)s")
logger = logging.getLogger("ecosystem_agent")

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "spBv1.0/DataCenter/NDATA/+")

BUFFER_SIZE = 100
IF_FIT_THRESHOLD = 50
IF_CONTAMINATION = 0.05

# Physics: HKUST-1 Two-Phase Immersion Limits
THRESHOLD_CHF_WARN = 150.0  
THRESHOLD_CHF_CRIT = 165.0  

# Economics: Smart Contract SLA Limits
THRESHOLD_UNSETTLED_WARN = 500.0   # USD equivalent in unpaid compute
THRESHOLD_UNSETTLED_CRIT = 1000.0  # USD equivalent — Smart contract is stalling!

# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class EcosystemTelemetry(BaseModel):
    """Telemetry separated by timescale: instantaneous physics vs accumulated economics."""
    # --- Physical Variables (Continuous, fast) ---
    heat_flux_w_cm2: float
    fluid_temp_c: float
    vapor_pressure_kpa: float
    water_harvested_liters: float
    
    # --- Economic Variables (Accumulating, slow settlement) ---
    compute_yield_usd: float
    unsettled_balance_usd: float = Field(
        ...,
        description="Accumulated value owed to the participant. Replaces instantaneous profit tracking."
    )
    timestamp: str

class PodStatus(BaseModel):
    pod_id: str
    latest_metrics: EcosystemTelemetry
    phys_model_fitted: bool
    econ_model_fitted: bool
    active_anomalies: List[str]
    status: str

class Anomaly(BaseModel):
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

# =============================================================================
# IN-MEMORY STATE STORE
# =============================================================================

class AgentState:
    def __init__(self) -> None:
        self.buffers: Dict[str, deque] = {}
        
        # Dual-Model Architecture
        self.phys_models: Dict[str, Optional[IsolationForest]] = {}
        self.econ_models: Dict[str, Optional[IsolationForest]] = {}
        
        self.phys_fitted: Dict[str, bool] = {}
        self.econ_fitted: Dict[str, bool] = {}
        
        self.anomalies: Dict[str, Anomaly] = {}
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
            self.buffers[pod_id].append(reading)

    def get_readings(self, pod_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.buffers[pod_id])

    def register_anomaly(self, anomaly: Anomaly) -> None:
        with self._lock:
            self.anomalies[anomaly.anomaly_id] = anomaly
        logger.warning(
            "ANOMALY [%s] pod=%s metric=%s value=%.2f method=%s",
            anomaly.severity.upper(), anomaly.pod_id, anomaly.metric, anomaly.value, anomaly.method,
        )

    def get_active_anomalies(self) -> List[Anomaly]:
        with self._lock:
            return [a for a in self.anomalies.values() if not a.acknowledged]

# =============================================================================
# ENSEMBLE ANOMALY DETECTION ENGINE
# =============================================================================

class AnomalyDetector:
    def __init__(self, state: AgentState) -> None:
        self.state = state

    def check_thresholds(self, pod_id: str, reading: Dict[str, Any]) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        ts = reading["timestamp"]

        # --- PHYSICS: HKUST-1 CHF ---
        heat_flux = reading.get("heat_flux_w_cm2", 0.0)
        if heat_flux >= THRESHOLD_CHF_CRIT:
            anomalies.append(self._make_anomaly(
                pod_id, "heat_flux_w_cm2", heat_flux, THRESHOLD_CHF_CRIT, "critical", "threshold", ts
            ))

        # --- ECONOMICS: SLA Breach ---
        unsettled = reading.get("unsettled_balance_usd", 0.0)
        if unsettled >= THRESHOLD_UNSETTLED_CRIT:
            anomalies.append(self._make_anomaly(
                pod_id, "unsettled_balance_usd", unsettled, THRESHOLD_UNSETTLED_CRIT, "critical", "threshold", ts
            ))
        elif unsettled >= THRESHOLD_UNSETTLED_WARN:
            anomalies.append(self._make_anomaly(
                pod_id, "unsettled_balance_usd", unsettled, THRESHOLD_UNSETTLED_WARN, "warning", "threshold", ts
            ))

        return anomalies

    def check_isolation_forests(self, pod_id: str, reading: Dict[str, Any]) -> List[Anomaly]:
        readings = self.state.get_readings(pod_id)
        if len(readings) < IF_FIT_THRESHOLD:
            return []

        anomalies = []
        
        # --- 1. PHYSICAL AI MODEL ---
        X_phys = np.array([[
            r.get("heat_flux_w_cm2", 100.0),
            r.get("fluid_temp_c", 55.0),
            r.get("vapor_pressure_kpa", 101.3),
            r.get("water_harvested_liters", 0.0)
        ] for r in readings], dtype=np.float32)

        if not self.state.phys_fitted[pod_id]:
            model = IsolationForest(n_estimators=100, contamination=IF_CONTAMINATION, random_state=42)
            model.fit(X_phys)
            with self.state._lock:
                self.state.phys_models[pod_id] = model
                self.state.phys_fitted[pod_id] = True
        else:
            x_latest = np.array([[
                reading.get("heat_flux_w_cm2", 100.0), reading.get("fluid_temp_c", 55.0),
                reading.get("vapor_pressure_kpa", 101.3), reading.get("water_harvested_liters", 0.0)
            ]], dtype=np.float32)
            if self.state.phys_models[pod_id].predict(x_latest)[0] == -1:
                anomalies.append(self._make_anomaly(
                    pod_id, "Physics_Decoupling", float(self.state.phys_models[pod_id].score_samples(x_latest)[0]), 
                    None, "critical", "isolation_forest_phys", reading["timestamp"]
                ))

        # --- 2. ECONOMIC AI MODEL ---
        X_econ = np.array([[
            r.get("compute_yield_usd", 0.0),
            r.get("unsettled_balance_usd", 0.0)
        ] for r in readings], dtype=np.float32)

        if not self.state.econ_fitted[pod_id]:
            model = IsolationForest(n_estimators=100, contamination=IF_CONTAMINATION, random_state=42)
            model.fit(X_econ)
            with self.state._lock:
                self.state.econ_models[pod_id] = model
                self.state.econ_fitted[pod_id] = True
        else:
            x_latest = np.array([[
                reading.get("compute_yield_usd", 0.0), reading.get("unsettled_balance_usd", 0.0)
            ]], dtype=np.float32)
            if self.state.econ_models[pod_id].predict(x_latest)[0] == -1:
                anomalies.append(self._make_anomaly(
                    pod_id, "Economic_Decoupling", float(self.state.econ_models[pod_id].score_samples(x_latest)[0]), 
                    None, "warning", "isolation_forest_econ", reading["timestamp"]
                ))

        return anomalies

    @staticmethod
    def _make_anomaly(pod_id: str, metric: str, value: float, threshold: Optional[float],
                      severity: str, method: str, timestamp: str) -> Anomaly:
        return Anomaly(
            anomaly_id=str(uuid.uuid4()), pod_id=pod_id, metric=metric,
            value=round(value, 3), threshold=threshold, severity=severity,
            method=method, detected_at=timestamp, acknowledged=False,
        )

# =============================================================================
# APP ENTRY
# =============================================================================
app_state = AgentState()
detector = AnomalyDetector(app_state)
app = FastAPI(title="Triple-Bottom-Line AI Agent")
# ... MQTT setup and FastAPI routing omitted for brevity ...
