import paho.mqtt.client as mqtt
import time
import json
import random
from datetime import datetime, timezone

# =============================================================================
# Triple-Bottom-Line Ecosystem Simulator
# Generates realistic physics (HKUST-1 heat flux, fluid temps) and economics 
# (compute yield, unsettled balances) for our immersion pods.
# =============================================================================

BROKER = "localhost"
PORT = 1883
PODS = ["Pod_A01", "Pod_A02", "Pod_A03"]

client = mqtt.Client(client_id="Ecosystem_Simulator")

def connect_mqtt():
    while True:
        try:
            client.connect(BROKER, PORT)
            print(f"Connected to MQTT Broker at {BROKER}:{PORT}")
            break
        except ConnectionRefusedError:
            print("Broker not ready, retrying in 3s...")
            time.sleep(3)

connect_mqtt()

# Track the accumulating economic balance for each pod
unsettled_balances = {pod: 0.0 for pod in PODS}

print("Starting Triple-Bottom-Line Telemetry Simulation...")
print("Press Ctrl+C to stop.")

try:
    while True:
        for pod in PODS:
            # 1. Simulate the Physics (Thermodynamics & Hydrology)
            # Nominal HKUST-1 heat flux is safe around 130-140 W/cm2
            heat_flux = random.uniform(120.0, 145.0) 
            
            # Simulate a Critical Heat Flux (CHF) failure on Pod_A01 occasionally
            if pod == "Pod_A01" and random.random() > 0.95:
                heat_flux = random.uniform(165.0, 172.0) # Dry-out imminent!
                
            fluid_temp = random.uniform(52.0, 59.0)
            vapor_pressure = random.uniform(101.3, 105.0)
            
            # The hotter the fluid, the more water the MOF-303 AWG generates
            water = heat_flux * 0.05 
            
            # 2. Simulate the Economics
            # Value of the compute (e.g., LLM training) correlates to the heat output
            compute_yield = heat_flux * 0.8
            unsettled_balances[pod] += compute_yield
            
            # Simulate Smart Contract payout
            distributed_profit = 0.0
            if random.random() > 0.8: # 20% chance to settle the balance
                distributed_profit = unsettled_balances[pod]
                unsettled_balances[pod] = 0.0
                
            # Simulate an Economic SLA Breach on Pod_A02
            # Force the balance to never settle, tripping the Anomaly Agent > $1000
            if pod == "Pod_A02" and unsettled_balances[pod] > 1050.0:
                distributed_profit = 0.0 # Smart contract frozen!
                
            payload = {
                "heat_flux_w_cm2": round(heat_flux, 2),
                "fluid_temp_c": round(fluid_temp, 2),
                "vapor_pressure_kpa": round(vapor_pressure, 2),
                "water_harvested_liters": round(water, 2),
                "compute_yield_usd": round(compute_yield, 2),
                "unsettled_balance_usd": round(unsettled_balances[pod], 2),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            
            topic = f"spBv1.0/DataCenter/NDATA/{pod}"
            client.publish(topic, json.dumps(payload))
            print(f"[{pod}] Flux: {payload['heat_flux_w_cm2']} W/cm2 | Unsettled: ${payload['unsettled_balance_usd']}")
            
        time.sleep(5) # Tick every 5 seconds
except KeyboardInterrupt:
    print("Simulation stopped.")
    client.disconnect()
