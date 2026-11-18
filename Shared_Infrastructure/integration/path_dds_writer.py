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


from http.server import HTTPServer, BaseHTTPRequestHandler
import hmac
matched = threading.Event()
class Listener(fastdds.DataWriterListener):
    def __init__(self):
        super().__init__()
    def on_publication_matched(self, writer, info):
        if info.current_count > 0:
            matched.set()
        else:
            matched.clear()
pubq = fastdds.PublisherQos()
participant.get_default_publisher_qos(pubq)
publisher = participant.create_publisher(pubq)
wq = fastdds.DataWriterQos()
publisher.get_default_datawriter_qos(wq)
configure(wq)
listener = Listener()
writer = publisher.create_datawriter(topic, wq, listener)
if writer is None:
    raise RuntimeError("Could not create persistent writer")
class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.reply(200 if self.path == "/ready" else 404, {"matched": matched.is_set()})
    def do_POST(self):
        if not hmac.compare_digest(self.headers.get("X-RONO-Token", ""), os.environ["RONO_AGENT_TOKEN"]):
            return self.reply(403, {"error": "Invalid token"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/publish" or not 0 < size <= 240:
                raise ValueError("Invalid publish request")
            payload = self.rfile.read(size).decode()
            intent = json.loads(payload)
            if intent.get("schema") != 1 or intent.get("action") not in ("direct", "alternative", "restore"):
                raise ValueError("Invalid intent")
            if not matched.is_set():
                raise RuntimeError("DDS receiver is not matched")
            sample = HelloWorld.HelloWorld()
            sample.index(0)
            sample.message(payload)
            if writer.write(sample) != 0:
                raise RuntimeError("DDS write failed")
            self.reply(200, {"published": True})
        except Exception as error:
            self.reply(400, {"error": str(error)})
server = HTTPServer(("127.0.0.1", 8514), Handler)
server.timeout = .5
try:
    while running:
        server.handle_request()
finally:
    server.server_close()
    participant.delete_contained_entities()
    factory.delete_participant(participant)
