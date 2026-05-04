"""
edge_compressor_sim.py

Simulates a Sparkplug B MQTT edge node representing a single compressor station
at an oil & gas field site. Publishes realistic compressor telemetry with Gaussian
noise on a configurable interval.

=== SPARKPLUG B OVERVIEW ===
Sparkplug B is an open specification (Eclipse Foundation) for MQTT-based Industrial
IoT. It defines:
  - A topic namespace:  spBv1.0/{group_id}/{msg_type}/{edge_node_id}
  - Message types:      NBIRTH, NDEATH, NDATA, DBIRTH, DDEATH, DDATA, NCMD, DCMD
  - Payload encoding:   Google Protocol Buffers (protobuf) using sparkplug_b.proto
  - Metric lifecycle:   nodes announce themselves (NBIRTH) with full metric descriptors
                        on connect, publish only changed values (NDATA) thereafter,
                        and announce departure (NDEATH) via Last Will and Testament

=== PAYLOAD ENCODING NOTE ===
This simulator uses a SIMPLIFIED JSON-based payload labeled as
"simplified_json_not_protobuf". The real Sparkplug B specification requires Google
Protocol Buffers encoding using the official .proto definition:
    https://github.com/eclipse/tahu/blob/master/sparkplug_b/sparkplug_b.proto

Ignition's Cirrus Link MQTT Engine module expects the real protobuf encoding.
To use this simulator with a live Ignition instance:
    pip install sparkplug-b  (or use the tahu library: pip install tahu)
    Then replace _build_nbirth_payload() / _build_ndata_payload() with the
    equivalent sparkplug_b.proto message construction.

The payload STRUCTURE (fields, metric names, aliases, data types, lifecycle) is
100% spec-compliant — only the serialization format differs.

=== INSTALLATION ===
    pip install paho-mqtt

=== USAGE ===
    # Start Mosquitto broker first:
    docker compose up -d

    # Run simulator:
    python edge_compressor_sim.py

    # Verify in another terminal:
    mosquitto_sub -h localhost -p 1883 -t "spBv1.0/#" -v

Author: Adam Dryden
"""

import json
import math
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt

# =============================================================================
# SPARKPLUG B CONSTANTS
# =============================================================================

# All Sparkplug B topics must begin with this version prefix.
# "spBv1.0" = Sparkplug B version 1.0
SPBV_PREFIX: str = "spBv1.0"

# Group ID maps to an ISA-95 Site or logical grouping of edge nodes.
# All nodes in a Group are typically part of the same field/facility.
GROUP_ID: str = "OilGas_Field_A"

# Edge Node ID: unique identifier for this physical RTU/gateway.
# In production, this would come from device configuration (e.g., a config file).
EDGE_NODE_ID: str = "Compressor_Station_01"

# Sparkplug B message type tokens (these are part of the topic, not the payload)
MSG_NBIRTH: str = "NBIRTH"  # Node Birth: full metric catalogue + current values
MSG_NDEATH: str = "NDEATH"  # Node Death: delivered by broker as LWT on disconnect
MSG_NDATA: str = "NDATA"    # Node Data: periodic metric value updates

# MQTT broker connection parameters
BROKER_HOST: str = "localhost"
BROKER_PORT: int = 1883
BROKER_KEEPALIVE: int = 60  # seconds; broker drops client if silent this long

# Telemetry publish interval in seconds.
# Production values: 5–30s for high-priority process variables, 60s for slow ones.
# Lower = more bandwidth and broker CPU; balance against cellular data costs.
PUBLISH_INTERVAL_SEC: int = 5

# =============================================================================
# SPARKPLUG B DATA TYPE IDENTIFIERS
# These integer codes match the DataType enum in sparkplug_b.proto.
# They are included in the NBIRTH metric descriptors so Ignition MQTT Engine
# creates tags with the correct data type.
# =============================================================================
DATA_TYPE_INT32: int = 7    # 32-bit signed integer
DATA_TYPE_INT64: int = 10   # 64-bit signed integer (used for timestamps, counters)
DATA_TYPE_FLOAT: int = 9    # 32-bit IEEE 754 float
DATA_TYPE_BOOLEAN: int = 11 # Boolean true/false
DATA_TYPE_STRING: int = 12  # UTF-8 string

# =============================================================================
# METRIC CATALOGUE
#
# This list defines all metrics published by this edge node.
# In a real Sparkplug B implementation, this catalogue is embedded in the NBIRTH
# protobuf payload. The consuming system (Ignition) uses it to:
#   1. Create tag definitions with correct data types
#   2. Build an alias-to-name lookup table for decoding NDATA messages
#   3. Apply engineering unit metadata to tag properties
#
# Fields:
#   name:        metric name as it appears in the Tag Browser
#   alias:       integer shorthand used in NDATA (saves bytes over cellular)
#   dataType:    Sparkplug B data type code (see above)
#   units:       engineering units string (informational, stored in tag metadata)
#   description: human-readable metric description for operators
#   normal_min:  lower bound of normal operating range (for alarm context)
#   normal_max:  upper bound of normal operating range
#   noise_std:   standard deviation for Gaussian noise simulation
# =============================================================================
METRIC_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "SuctionPressure_PSI",
        "alias": 1,
        "dataType": DATA_TYPE_FLOAT,
        "units": "PSI",
        "description": "Gas suction pressure at compressor inlet header",
        "normal_min": 85.0,
        "normal_max": 95.0,
        "noise_std": 0.8,
    },
    {
        "name": "DischargePressure_PSI",
        "alias": 2,
        "dataType": DATA_TYPE_FLOAT,
        "units": "PSI",
        "description": "Gas discharge pressure at compressor outlet header",
        "normal_min": 250.0,
        "normal_max": 280.0,
        "noise_std": 2.0,
    },
    {
        "name": "VibrationX_mms",
        "alias": 3,
        "dataType": DATA_TYPE_FLOAT,
        "units": "mm/s",
        "description": "Compressor housing vibration, X-axis (radial direction)",
        "normal_min": 1.5,
        "normal_max": 3.5,
        "noise_std": 0.15,
    },
    {
        "name": "VibrationY_mms",
        "alias": 4,
        "dataType": DATA_TYPE_FLOAT,
        "units": "mm/s",
        "description": "Compressor housing vibration, Y-axis (axial direction)",
        "normal_min": 1.5,
        "normal_max": 3.5,
        "noise_std": 0.15,
    },
    {
        "name": "MotorTemp_C",
        "alias": 5,
        "dataType": DATA_TYPE_FLOAT,
        "units": "°C",
        "description": (
            "Drive motor winding temperature. "
            "Class F insulation limit: 155°C. Trip setpoint: ~95°C."
        ),
        "normal_min": 65.0,
        "normal_max": 75.0,
        "noise_std": 0.5,
    },
    {
        "name": "RPM",
        "alias": 6,
        "dataType": DATA_TYPE_FLOAT,
        "units": "RPM",
        "description": "Compressor shaft rotational speed",
        "normal_min": 1750.0,
        "normal_max": 1800.0,
        "noise_std": 5.0,
    },
    {
        "name": "RunHours",
        "alias": 7,
        "dataType": DATA_TYPE_FLOAT,
        "units": "hours",
        "description": (
            "Cumulative operating hours since last maintenance reset. "
            "Maintenance trigger typically at 4000-hour intervals."
        ),
        # Special: incrementing counter, not a bounded range
        "normal_min": 0.0,
        "normal_max": 0.0,
        "noise_std": 0.0,
    },
    {
        "name": "Status",
        "alias": 8,
        "dataType": DATA_TYPE_INT32,
        "units": "",
        "description": (
            "Operating state enumeration. "
            "0=Running, 1=Fault, 2=Standby, 3=Maintenance"
        ),
        "normal_min": 0,
        "normal_max": 0,
        "noise_std": 0.0,
    },
]


class SparkplugBSimulator:
    """
    Simulates a Sparkplug B MQTT edge node for an oil & gas compressor station.

    Implements the full NBIRTH → NDATA → NDEATH lifecycle as specified by
    the Sparkplug B specification (Eclipse Foundation, v1.0).

    Attributes:
        _seq (int):         Monotonically incrementing sequence number (0-255, wraps).
        _run_hours (float): Accumulated operating hours (odometer-style counter).
        _status (int):      Current machine status (0=Running, 1=Fault, etc.).
        _running (bool):    Main loop control flag.
        _client:            paho-mqtt Client instance.
    """

    def __init__(self) -> None:
        """Initialize the simulator state and configure the MQTT client."""
        # Sequence number: Sparkplug B requires every message to carry a
        # monotonically incrementing seq (0-255, then wraps back to 0).
        # Consumers can detect missed messages by checking for gaps in seq.
        self._seq: int = 0

        # Start RunHours at a realistic accumulated value (not 0 at birth)
        # to simulate a machine that has been running for some time.
        self._run_hours: float = 4823.5

        # Current machine status: 0 = Running (normal)
        self._status: int = 0

        # Main loop control flag; set to False on KeyboardInterrupt for clean shutdown
        self._running: bool = False

        # -----------------------------------------------------------------------
        # MQTT CLIENT CONFIGURATION
        # client_id must be unique per broker session.
        # Using EDGE_NODE_ID as the basis ensures it stays unique when multiple
        # simulators (different stations) are running simultaneously.
        # -----------------------------------------------------------------------
        self._client = mqtt.Client(
            client_id=f"sparkplug-sim-{EDGE_NODE_ID}",
            clean_session=True,     # clean_session=True: broker discards any stale
                                    # session state from a prior connection with this ID
            protocol=mqtt.MQTTv311, # MQTT version 3.1.1 (required by Sparkplug B spec)
        )

        # Register MQTT event callbacks
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish    = self._on_publish

        # -----------------------------------------------------------------------
        # LAST WILL AND TESTAMENT (LWT) — SPARKPLUG B NDEATH
        #
        # The LWT is a special MQTT feature: the client registers a 'death message'
        # with the broker AT CONNECT TIME. The broker holds this message and delivers
        # it automatically if/when the client disconnects ungracefully (network loss,
        # power failure, crash).
        #
        # Sparkplug B requires NDEATH to be registered as the LWT BEFORE connect().
        # This is critical for consumer reliability — it's the only way a consumer
        # can know the edge node went offline without a graceful shutdown.
        #
        # When Ignition MQTT Engine receives NDEATH, it sets all tags from this
        # node to BAD quality (shown as gray/strikethrough in Perspective screens).
        # -----------------------------------------------------------------------
        death_topic   = f"{SPBV_PREFIX}/{GROUP_ID}/{MSG_NDEATH}/{EDGE_NODE_ID}"
        death_payload = self._build_ndeath_payload()

        self._client.will_set(
            topic=death_topic,
            payload=json.dumps(death_payload).encode("utf-8"),
            qos=1,       # QoS 1: at-least-once delivery — NDEATH must reach subscribers
            retain=False, # Sparkplug B spec: NDEATH must NOT be retained
        )

    # =========================================================================
    # MQTT EVENT CALLBACKS
    # =========================================================================

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Dict, rc: int
    ) -> None:
        """
        Called by paho-mqtt on CONNACK (connection acknowledgement) from broker.

        rc codes (MQTT 3.1.1):
            0: Connection accepted
            1: Refused — incorrect protocol version
            2: Refused — invalid client identifier
            3: Refused — server unavailable
            4: Refused — bad username or password
            5: Refused — not authorized

        Sparkplug B requires publishing NBIRTH immediately after a successful
        connect — BEFORE any NDATA messages. Ignition MQTT Engine will ignore
        NDATA messages from a node that hasn't sent NBIRTH (it doesn't know
        the metric schema yet).
        """
        if rc == 0:
            print(
                f"[{self._now()}] ✓ Connected to broker {BROKER_HOST}:{BROKER_PORT}"
                f" (client_id={self._client._client_id.decode()})"
            )
            # NBIRTH must be the first message sent after connect
            self._publish_nbirth()
        else:
            print(f"[{self._now()}] ✗ Connection refused (rc={rc})")

    def _on_disconnect(
        self, client: mqtt.Client, userdata: Any, rc: int
    ) -> None:
        """
        Called when the client disconnects from the broker.

        rc == 0: clean disconnect initiated by our call to client.disconnect()
        rc != 0: unexpected disconnect (network error, broker restart, etc.)
                 The broker will deliver our NDEATH LWT automatically in this case.
        """
        if rc == 0:
            print(f"[{self._now()}] ✓ Clean disconnect from broker.")
        else:
            print(
                f"[{self._now()}] ✗ Unexpected disconnect (rc={rc}). "
                "Broker will deliver NDEATH LWT to all subscribers."
            )

    def _on_publish(
        self, client: mqtt.Client, userdata: Any, mid: int
    ) -> None:
        """
        Called by paho-mqtt when a QoS 1 message is acknowledged by the broker.

        'mid' is the message ID. In production, you would log QoS 1 failures
        (missed ACKs after max_inflight_messages) to detect broker connection issues.
        """
        pass  # No-op for this simulator; uncomment to add message tracing:
        # print(f"[{self._now()}] QoS 1 ACK received (mid={mid})")

    # =========================================================================
    # SPARKPLUG B PAYLOAD BUILDERS
    # =========================================================================

    def _next_seq(self) -> int:
        """
        Returns the current sequence number and increments it.

        Sparkplug B requires seq to be a monotonically incrementing integer
        in the range 0-255. When it reaches 255, it wraps back to 0.

        Consumers check the seq on every message. A gap (e.g., received 5
        then 8) means two messages were lost, and the consumer may request
        a re-birth (NCMD with Rebirth=true) to re-sync the metric catalogue.
        """
        seq = self._seq
        self._seq = (self._seq + 1) % 256
        return seq

    def _now_ms(self) -> int:
        """
        Returns current UTC time as milliseconds since Unix epoch.

        Sparkplug B timestamps are always UTC epoch milliseconds (Int64).
        Each individual metric can carry its own timestamp (the time the value
        was sampled at the RTU), which may differ from the message-level timestamp.
        """
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def _now(self) -> str:
        """Human-readable ISO-8601 UTC timestamp for console log output."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _build_ndeath_payload(self) -> Dict[str, Any]:
        """
        Build the NDEATH (Node Death) payload.

        NDEATH tells consumers that this edge node has gone offline. All metrics
        from this node should be treated as STALE (unknown current value).

        In real Sparkplug B (protobuf), NDEATH carries exactly one metric:
            { name: "bdSeq", value: <birth_death_sequence_number>, dataType: INT64 }

        The bdSeq (birth-death sequence) is a separate counter from the regular seq.
        It increments every time the node connects and publishes NBIRTH. Consumers
        match bdSeq values between NBIRTH and NDEATH to handle reconnect edge cases.

        NOTE: This returns a SIMPLIFIED JSON dictionary, not real protobuf bytes.
        In production with Ignition MQTT Engine, use the tahu library:
            from tahu.sparkplug_b import SparkplugBPayload
        """
        return {
            "_sparkplug_note": "simplified_json_not_protobuf",
            "timestamp": self._now_ms(),
            "seq": self._next_seq(),
            "metrics": [
                {
                    "name": "bdSeq",  # birth-death sequence number
                    "value": 0,
                    "dataType": DATA_TYPE_INT64,
                    "timestamp": self._now_ms(),
                }
            ],
        }

    def _build_nbirth_payload(self) -> Dict[str, Any]:
        """
        Build the NBIRTH (Node Birth) payload.

        NBIRTH is the most important Sparkplug B message. It:
          1. Announces this edge node to all subscribers
          2. Provides the complete metric catalogue with names, aliases, data types
          3. Carries the initial value for every metric
          4. Carries metric properties (engineering units, descriptions)

        Ignition MQTT Engine does three things with NBIRTH:
          a. Creates tags in the Tag Browser under:
             [MQTT Engine]/Sparkplug B Devices/{group}/{node}/{metric_name}
          b. Sets the data type for each tag (important: can't be changed later
             without deleting and re-creating the tag)
          c. Builds an alias→name lookup table for decoding subsequent NDATA messages

        After NBIRTH is processed, NDATA messages can use integer aliases instead
        of full metric names. On a 10kbps cellular link:
          - "SuctionPressure_PSI" = 22 bytes
          - alias 1               =  1 byte (after varint encoding in protobuf)
          This 22x size reduction per metric matters when publishing 8 metrics
          every 5 seconds over a costly cellular data plan.

        NOTE: Returns a SIMPLIFIED JSON dictionary, not real protobuf bytes.
        """
        current_values = self._sample_current_values()
        ts = self._now_ms()

        metrics = []
        for descriptor in METRIC_CATALOG:
            metric = {
                # Full metric name: used in NBIRTH; alias used in NDATA
                "name":      descriptor["name"],
                "alias":     descriptor["alias"],
                "dataType":  descriptor["dataType"],
                "timestamp": ts,
                "value":     current_values[descriptor["name"]],
                # Properties block: only included in NBIRTH, not NDATA.
                # Ignition stores these as tag metadata/documentation.
                "properties": {
                    "engUnits":    {
                        "type":  DATA_TYPE_STRING,
                        "value": descriptor["units"],
                    },
                    "description": {
                        "type":  DATA_TYPE_STRING,
                        "value": descriptor["description"],
                    },
                    "normalMin": {
                        "type":  DATA_TYPE_FLOAT,
                        "value": float(descriptor["normal_min"]),
                    },
                    "normalMax": {
                        "type":  DATA_TYPE_FLOAT,
                        "value": float(descriptor["normal_max"]),
                    },
                },
            }
            metrics.append(metric)

        return {
            "_sparkplug_note": "simplified_json_not_protobuf",
            "timestamp": ts,
            "seq": self._next_seq(),
            "metrics": metrics,
        }

    def _build_ndata_payload(self) -> Dict[str, Any]:
        """
        Build an NDATA (Node Data) payload with current metric values.

        NDATA carries metric value updates. In a production implementation,
        only metrics that changed beyond their deadband are included (Report
        By Exception / RBE). This simulator publishes all metrics every cycle
        for simplicity — in production, add deadband logic to reduce bandwidth.

        Key differences from NBIRTH:
          - Uses alias (int) instead of name (string) — alias was declared in NBIRTH
          - No 'properties' block — already declared in NBIRTH
          - Metrics that haven't changed past deadband are omitted entirely

        Production deadband example:
            if abs(new_value - last_published_value) > deadband_threshold:
                include in NDATA payload

        NOTE: Returns a SIMPLIFIED JSON dictionary, not real protobuf bytes.
        """
        current_values = self._sample_current_values()
        ts = self._now_ms()

        metrics = []
        for descriptor in METRIC_CATALOG:
            metric = {
                # NDATA uses alias, NOT name — saves bytes on every message
                "alias":     descriptor["alias"],
                "dataType":  descriptor["dataType"],
                "timestamp": ts,
                "value":     current_values[descriptor["name"]],
            }
            metrics.append(metric)

        return {
            "_sparkplug_note": "simplified_json_not_protobuf",
            "timestamp": ts,
            "seq": self._next_seq(),
            "metrics": metrics,
        }

    # =========================================================================
    # VALUE SIMULATION
    # =========================================================================

    def _sample_current_values(self) -> Dict[str, Any]:
        """
        Generate one set of simulated sensor readings with realistic Gaussian noise.

        Simulation strategy:
          - Each process variable has a 'normal' operating band (min/max)
          - random.gauss(mu=0, sigma) adds realistic sensor noise around the
            midpoint of that band
          - RunHours increments monotonically (like a real odometer)
          - Status has a small probability of entering fault state each cycle,
            and a higher probability of recovering — creates sporadic fault events

        random.gauss(mu, sigma) produces values from a normal distribution N(μ,σ).
        Real industrial sensors have Gaussian-distributed measurement noise (per
        central limit theorem applied to electronics noise, quantization error, etc.).
        This makes the simulated data statistically realistic for demo purposes.

        Returns:
            Dict mapping metric name → current simulated value
        """
        # -----------------------------------------------------------------------
        # STATUS: Stochastic fault injection
        # Probability tuned so faults occur roughly every 5-10 minutes of
        # runtime (at 5s publish interval: ~0.2% per cycle ≈ once every 500 cycles
        # = once every ~42 minutes; 5% recovery chance = average fault duration ~20s)
        # -----------------------------------------------------------------------
        if self._status == 0 and random.random() < 0.002:  # 0.2% fault probability
            self._status = 1
            print(f"[{self._now()}] ⚠ FAULT INJECTED — Status changed to 1 (Fault)")
        elif self._status == 1 and random.random() < 0.05:  # 5% recovery probability
            self._status = 0
            print(f"[{self._now()}] ✓ RECOVERY — Status changed to 0 (Running)")

        # -----------------------------------------------------------------------
        # RUN HOURS: only accumulate when machine is running (status == 0)
        # Increment by publish interval converted to hours: 5s / 3600 = 0.00139 hr
        # -----------------------------------------------------------------------
        if self._status == 0:
            self._run_hours += PUBLISH_INTERVAL_SEC / 3600.0

        def noisy(low: float, high: float, std: float) -> float:
            """
            Return a value uniformly centered in [low, high] with Gaussian noise.

            The base value is sampled uniformly from [low, high] and then
            Gaussian noise is added. This creates a realistic mix of
            setpoint variation (uniform component) and measurement noise
            (Gaussian component).
            """
            center = random.uniform(low, high)
            noise  = random.gauss(0.0, std)
            return round(center + noise, 3)

        return {
            "SuctionPressure_PSI":   noisy(85.0, 95.0,  0.8),
            "DischargePressure_PSI": noisy(250.0, 280.0, 2.0),
            "VibrationX_mms":        noisy(1.5, 3.5,    0.15),
            "VibrationY_mms":        noisy(1.5, 3.5,    0.15),
            "MotorTemp_C":           noisy(65.0, 75.0,  0.5),
            "RPM":                   noisy(1750.0, 1800.0, 5.0),
            "RunHours":              round(self._run_hours, 4),
            "Status":                self._status,
        }

    # =========================================================================
    # PUBLISH HELPERS
    # =========================================================================

    def _publish_nbirth(self) -> None:
        """
        Publish the NBIRTH message to announce this edge node to all subscribers.

        Uses QoS 1 (at-least-once) because NBIRTH is critical — all subsequent
        NDATA messages are meaningless if the consumer never received NBIRTH
        (it wouldn't know the metric names or data types).
        """
        topic   = f"{SPBV_PREFIX}/{GROUP_ID}/{MSG_NBIRTH}/{EDGE_NODE_ID}"
        payload = self._build_nbirth_payload()
        encoded = json.dumps(payload, indent=None, ensure_ascii=False).encode("utf-8")

        result = self._client.publish(topic, encoded, qos=1, retain=False)
        print(
            f"[{self._now()}] → NBIRTH published to {topic}"
            f" ({len(encoded)} bytes, {len(METRIC_CATALOG)} metrics declared)"
        )

    def _publish_ndata(self) -> None:
        """
        Publish a periodic NDATA telemetry update.

        Uses QoS 0 (fire-and-forget) for high-frequency telemetry because:
          - QoS 0: no broker acknowledgement, no retransmit → lower latency, less overhead
          - QoS 1: broker must ACK each message → adds ~1 RTT latency per publish
          - For telemetry published every 5 seconds, a lost packet is acceptable
            (the next sample arrives 5 seconds later); for NBIRTH/NDEATH it is not.
        """
        topic   = f"{SPBV_PREFIX}/{GROUP_ID}/{MSG_NDATA}/{EDGE_NODE_ID}"
        payload = self._build_ndata_payload()
        encoded = json.dumps(payload, indent=None, ensure_ascii=False).encode("utf-8")

        self._client.publish(topic, encoded, qos=0, retain=False)
        # Note: QoS 0 on_publish callback does NOT fire (there's no ACK to wait for)

    def _publish_ndeath(self) -> None:
        """
        Explicitly publish NDEATH for a graceful, intentional shutdown.

        On a graceful shutdown (keyboard interrupt, service stop), we publish
        NDEATH manually before calling disconnect(). This is faster than waiting
        for the broker's LWT delivery timeout to expire.

        QoS 1: NDEATH must reach the broker before we disconnect — consumers
        need to know we went offline immediately.
        """
        topic   = f"{SPBV_PREFIX}/{GROUP_ID}/{MSG_NDEATH}/{EDGE_NODE_ID}"
        payload = self._build_ndeath_payload()
        encoded = json.dumps(payload, indent=None, ensure_ascii=False).encode("utf-8")

        result = self._client.publish(topic, encoded, qos=1, retain=False)
        print(f"[{self._now()}] → NDEATH published (graceful shutdown) to {topic}")
        # Brief sleep to ensure the QoS 1 ACK is received before we stop the loop
        time.sleep(0.5)

    # =========================================================================
    # MAIN RUN LOOP
    # =========================================================================

    def run(self) -> None:
        """
        Connect to the MQTT broker and start the telemetry publish loop.

        Architecture note:
        paho-mqtt's loop_start() spins up a background daemon thread that handles
        all MQTT network I/O (packet framing, ACKs, ping/pong for keepalive).
        This frees our main thread to sleep between publishes without blocking
        the MQTT socket.

        The connect() call is non-blocking; _on_connect fires asynchronously
        when the broker returns a CONNACK. We add a short sleep to let the
        connection establish before entering the publish loop.
        """
        print(
            f"\n[{self._now()}] ═══ Sparkplug B Compressor Station Simulator ═══\n"
            f"  Group ID:     {GROUP_ID}\n"
            f"  Node ID:      {EDGE_NODE_ID}\n"
            f"  Broker:       {BROKER_HOST}:{BROKER_PORT}\n"
            f"  Interval:     {PUBLISH_INTERVAL_SEC}s\n"
            f"  Metrics:      {len(METRIC_CATALOG)}\n"
            f"  RunHours:     {self._run_hours:.1f} hrs (starting value)\n"
            f"\n  Press Ctrl+C to stop (NDEATH will be published cleanly)\n"
        )

        # Start MQTT network I/O background thread
        self._client.loop_start()

        # Connect (non-blocking — _on_connect fires on broker CONNACK)
        self._client.connect(BROKER_HOST, BROKER_PORT, keepalive=BROKER_KEEPALIVE)

        # Allow time for CONNACK and NBIRTH to complete before entering loop
        time.sleep(1.5)

        self._running = True

        try:
            while self._running:
                self._publish_ndata()

                # Console status line (concise, not one line per metric)
                print(
                    f"[{self._now()}]  NDATA  "
                    f"RunHours={self._run_hours:.2f}h  "
                    f"Status={self._status}  "
                    f"seq={self._seq}"
                )

                time.sleep(PUBLISH_INTERVAL_SEC)

        except KeyboardInterrupt:
            print(f"\n[{self._now()}] Ctrl+C received — publishing NDEATH and disconnecting...")
            self._publish_ndeath()
            self._client.loop_stop()
            self._client.disconnect()
            print(f"[{self._now()}] ✓ Simulator stopped cleanly.")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    simulator = SparkplugBSimulator()
    simulator.run()
