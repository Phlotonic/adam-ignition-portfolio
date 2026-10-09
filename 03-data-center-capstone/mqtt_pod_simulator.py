"""
mqtt_pod_simulator.py

Simulates multi-pod telemetry for the Regenerative Data Center Capstone.
Publishes thermodynamic, hydrological, and economic metrics over Sparkplug B / JSON
topics to Eclipse Mosquitto for ingestion by the AI Anomaly Agent and Ignition SCADA.
"""

from datetime import datetime, timezone
import json
import logging
import os
import random
import signal
import sys
import time

import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("pod_simulator")

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", "5.0"))
PODS = ["Pod_A01", "Pod_A02", "Pod_A03"]

running = True


def handle_stop_signal(signum, frame):
    global running
    logger.info("Termination signal received. Shutting down simulator...")
    running = False


signal.signal(signal.SIGINT, handle_stop_signal)
signal.signal(signal.SIGTERM, handle_stop_signal)


def create_mqtt_client() -> mqtt.Client:
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="Ecosystem_Simulator",
        )
    except AttributeError:
        client = mqtt.Client(client_id="Ecosystem_Simulator")
    return client


def main():
    logger.info("Starting Triple-Bottom-Line Pod Simulator targeting %s:%d ...", BROKER_HOST, BROKER_PORT)
    client = create_mqtt_client()

    # Retry connection with backoff
    connected = False
    while running and not connected:
        try:
            client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
            client.loop_start()
            connected = True
            logger.info("Connected to MQTT broker at %s:%d", BROKER_HOST, BROKER_PORT)
        except Exception as e:
            logger.warning("Broker not ready (%s). Retrying in 3s...", e)
            time.sleep(3)

    unsettled_balances = {pod: 0.0 for pod in PODS}

    while running:
        for pod in PODS:
            # 1. Thermodynamics: Nominal HKUST-1 heat flux sits at 130–145 W/cm2
            # Critical Heat Flux limit on copper mesh is ~170 W/cm2
            heat_flux = random.uniform(125.0, 142.0)

            # Occasionally inject an anomaly on Pod_A01 to test CHF warning/critical detection
            if pod == "Pod_A01" and random.random() > 0.92:
                heat_flux = random.uniform(166.0, 172.0)  # CHF breach!

            fluid_temp = random.uniform(53.0, 58.5)
            vapor_pressure = random.uniform(101.3, 104.5)

            # 2. Hydrology: MOF-303 AWG water desorption yield
            water_harvested = round(heat_flux * 0.05 + random.uniform(0.1, 0.5), 2)

            # 3. Economics: Compute generation & participant payout
            compute_yield = round(heat_flux * 0.82 + random.uniform(2.0, 8.0), 2)
            unsettled_balances[pod] += compute_yield

            # Payout settlement event
            distributed_profit = 0.0
            if pod != "Pod_A02" and random.random() > 0.75:
                distributed_profit = round(unsettled_balances[pod], 2)
                unsettled_balances[pod] = 0.0

            # On Pod_A02, simulate a frozen smart contract payout leading to SLA breach (> $1000)
            if pod == "Pod_A02" and unsettled_balances[pod] > 1100.0:
                distributed_profit = round(unsettled_balances[pod], 2)
                unsettled_balances[pod] = 0.0  # Reset after triggering breach

            payload = {
                "node_id": pod,
                "participant_id": f"Wallet_{pod}",
                "heat_flux_w_cm2": round(heat_flux, 2),
                "fluid_temp_c": round(fluid_temp, 2),
                "vapor_pressure_kpa": round(vapor_pressure, 2),
                "water_harvested_liters": water_harvested,
                "compute_yield_usd": compute_yield,
                "distributed_profit_usd": distributed_profit,
                "unsettled_balance_usd": round(unsettled_balances[pod], 2),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            topic = f"spBv1.0/DataCenter/NDATA/{pod}"
            client.publish(topic, json.dumps(payload), qos=1)
            logger.info(
                "[%s] HeatFlux: %.1f W/cm2 | FluidTemp: %.1f°C | Water: %.1f L | Unsettled: $%.2f",
                pod,
                payload["heat_flux_w_cm2"],
                payload["fluid_temp_c"],
                payload["water_harvested_liters"],
                payload["unsettled_balance_usd"],
            )

        time.sleep(PUBLISH_INTERVAL)

    client.loop_stop()
    client.disconnect()
    logger.info("Simulator gracefully stopped.")


if __name__ == "__main__":
    main()
