"""DDS adapter for a versioned JSON PathIntent envelope in the existing HelloWorld type.

The topic is separate from RoutePolicyUpdate; authorized is never overloaded as a path choice.
"""
import argparse
import json
import os
import signal
import threading
import time
import urllib.request
import fastdds
import HelloWorld

parser = argparse.ArgumentParser()
parser.add_argument("role", choices=["receiver", "sender"])
parser.add_argument("payload", nargs="?")
args = parser.parse_args()
running = True


def stop(*unused):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
factory = fastdds.DomainParticipantFactory.get_instance()
pq = fastdds.DomainParticipantQos()
factory.get_default_participant_qos(pq)
participant = factory.create_participant(174, pq)
if participant is None:
    raise RuntimeError("Could not create participant")
data_type = HelloWorld.HelloWorldPubSubType()
data_type.set_name("RONOPathIntentEnvelope")
support = fastdds.TypeSupport(data_type)
participant.register_type(support)
tq = fastdds.TopicQos()
participant.get_default_topic_qos(tq)
topic = participant.create_topic("RONO_PathIntent", data_type.get_name(), tq)
if topic is None:
    raise RuntimeError("Could not create topic")


def configure(qos):
    qos.reliability().kind = fastdds.RELIABLE_RELIABILITY_QOS
    qos.durability().kind = fastdds.VOLATILE_DURABILITY_QOS
    qos.history().kind = fastdds.KEEP_LAST_HISTORY_QOS
    qos.history().depth = 20


try:
    if args.role == "receiver":
        sq = fastdds.SubscriberQos()
        participant.get_default_subscriber_qos(sq)
        subscriber = participant.create_subscriber(sq)
        rq = fastdds.DataReaderQos()
        subscriber.get_default_datareader_qos(rq)
        configure(rq)
        reader = subscriber.create_datareader(topic, rq)
        if reader is None:
            raise RuntimeError("Could not create reader")
        print(json.dumps({"event": "path_receiver_ready", "domain": 174}), flush=True)
        while running:
            sample = HelloWorld.HelloWorld()
            info = fastdds.SampleInfo()
            if reader.take_next_sample(sample, info) != 0:
                time.sleep(0.02)
                continue
            if not info.valid_data:
                continue
            try:
                payload = json.loads(sample.message())
                if (not isinstance(payload, dict) or payload.get("schema") != 1 or
                        payload.get("action") not in ("direct", "alternative", "restore")):
                    raise ValueError("Invalid intent envelope")
                # Both transports enter the same host-side agent endpoint.
                request = urllib.request.Request(
                    "http://127.0.0.1:8502/api/path-intents", method="POST",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json",
                             "X-RONO-Token": os.environ["RONO_AGENT_TOKEN"]})
                with urllib.request.urlopen(request, timeout=180) as response:
                    result = json.load(response)
                print(json.dumps({"event": "path_intent_result",
                                  "request_id": payload.get("request_id"),
                                  "status": result.get("status")}), flush=True)
            except Exception as error:
                print(json.dumps({"event": "path_receiver_error", "error": str(error)}), flush=True)
    else:
        if args.payload is None or len(args.payload.encode()) > 240:
            raise ValueError("Missing or oversized payload")
        matched = threading.Event()

        class Listener(fastdds.DataWriterListener):
            def __init__(self):
                super().__init__()

            def on_publication_matched(self, writer, info):
                if info.current_count_change > 0:
                    matched.set()

        pubq = fastdds.PublisherQos()
        participant.get_default_publisher_qos(pubq)
        publisher = participant.create_publisher(pubq)
        wq = fastdds.DataWriterQos()
        publisher.get_default_datawriter_qos(wq)
        configure(wq)
        listener = Listener()
        writer = publisher.create_datawriter(topic, wq, listener)
        if writer is None:
            raise RuntimeError("Could not create writer")
        if not matched.wait(15):
            raise RuntimeError("DDS receiver discovery timed out")
        sample = HelloWorld.HelloWorld()
        sample.index(0)
        sample.message(args.payload)
        result = writer.write(sample)
        print(json.dumps({"event": "path_intent_write", "result": str(result)}), flush=True)
        if result != 0:
            raise RuntimeError("DDS write failed")
        # The host waits for verified agent completion separately.
        time.sleep(2)
finally:
    participant.delete_contained_entities()
    factory.delete_participant(participant)
