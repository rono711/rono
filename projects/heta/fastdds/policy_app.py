import argparse
import json
import signal
import time

import fastdds
import RoutePolicyUpdate as policy

running = True


def stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

parser = argparse.ArgumentParser()
parser.add_argument("role", choices=["publisher", "subscriber"])
parser.add_argument("--domain", type=int, default=72)
parser.add_argument("--customer", type=int, default=65005)
parser.add_argument("--provider", type=int, default=65002)
parser.add_argument("--version", type=int, default=1)
parser.add_argument("--action", choices=["authorize", "revoke"], default="authorize")
parser.add_argument("--filter-customer", type=int, default=None)
parser.add_argument("--reliability",
                    choices=["reliable", "best_effort"], default="reliable")
parser.add_argument("--durability",
                    choices=["transient_local", "volatile"],
                    default="transient_local")
parser.add_argument("--history-depth", type=int, default=20)
args = parser.parse_args()
if not 1 <= args.history_depth <= 100:
    parser.error("--history-depth must be between 1 and 100")

factory = fastdds.DomainParticipantFactory.get_instance()
participant_qos = fastdds.DomainParticipantQos()
factory.get_default_participant_qos(participant_qos)
participant = factory.create_participant(args.domain, participant_qos)
if participant is None:
    raise RuntimeError("Could not create DDS participant")

data_type = policy.RoutePolicyUpdatePubSubType()
support = fastdds.TypeSupport(data_type)
participant.register_type(support)

topic_qos = fastdds.TopicQos()
participant.get_default_topic_qos(topic_qos)
topic = participant.create_topic(
    "RONO_RoutePolicyUpdate", data_type.get_name(), topic_qos
)
if topic is None:
    raise RuntimeError("Could not create DDS topic")


def configure_qos(qos):
    qos.reliability().kind = (
        fastdds.RELIABLE_RELIABILITY_QOS
        if args.reliability == "reliable"
        else fastdds.BEST_EFFORT_RELIABILITY_QOS
    )
    qos.durability().kind = (
        fastdds.TRANSIENT_LOCAL_DURABILITY_QOS
        if args.durability == "transient_local"
        else fastdds.VOLATILE_DURABILITY_QOS
    )
    qos.history().kind = fastdds.KEEP_LAST_HISTORY_QOS
    qos.history().depth = args.history_depth
    limits = qos.resource_limits()
    if limits.max_samples_per_instance != -1:
        limits.max_samples_per_instance = max(
            limits.max_samples_per_instance, args.history_depth)
    if limits.max_samples != -1:
        limits.max_samples = max(limits.max_samples, args.history_depth)

    print(json.dumps({
        "event": "qos_configured",
        "role": args.role,
        "reliability": args.reliability,
        "durability": args.durability,
        "history_depth": args.history_depth,
    }), flush=True)


try:
    if args.role == "publisher":
        publisher_qos = fastdds.PublisherQos()
        participant.get_default_publisher_qos(publisher_qos)
        publisher = participant.create_publisher(publisher_qos)

        writer_qos = fastdds.DataWriterQos()
        publisher.get_default_datawriter_qos(writer_qos)
        configure_qos(writer_qos)
        writer = publisher.create_datawriter(topic, writer_qos)
        if writer is None:
            raise RuntimeError("Could not create DDS writer")

        # Keep the writer alive after publishing so discovery and delivery
        # can complete. A bounded discovery/acknowledgment check comes next.
        time.sleep(2)

        sample = policy.RoutePolicyUpdate()
        sample.customer_asn(args.customer)
        sample.provider_asn(args.provider)
        sample.authorized(args.action == "authorize")
        sample.version(args.version)
        sample.published_timestamp_ns(time.time_ns())

        result = writer.write(sample)
        print(json.dumps({
            "event": "write_return",
            "result": str(result),
            "customer_asn": args.customer,
            "provider_asn": args.provider,
            "authorized": args.action == "authorize",
            "version": args.version,
        }), flush=True)

        deadline = time.monotonic() + 5
        while running and time.monotonic() < deadline:
            time.sleep(0.1)

    else:
        subscriber_qos = fastdds.SubscriberQos()
        participant.get_default_subscriber_qos(subscriber_qos)
        subscriber = participant.create_subscriber(subscriber_qos)

        reader_qos = fastdds.DataReaderQos()
        subscriber.get_default_datareader_qos(reader_qos)
        configure_qos(reader_qos)
        reader_topic = topic
        filtered_topic = None

        if args.filter_customer is not None:
            if not 1 <= args.filter_customer <= 4294967295:
                raise ValueError("Invalid filter customer ASN")

            expression = f"customer_asn = {args.filter_customer}"
            filtered_topic = participant.create_contentfilteredtopic(
                "RONO_FilteredPolicy",
                topic,
                expression,
                [],
            )
            if filtered_topic is None:
                raise RuntimeError("Could not create DDS content filter")

            reader_topic = filtered_topic
            print(json.dumps({
                "event": "filter_enabled",
                "expression": expression,
            }), flush=True)

        reader = subscriber.create_datareader(reader_topic, reader_qos)
        if reader is None:
            raise RuntimeError("Could not create DDS reader")

        cache = {}
        print("Policy subscriber ready; Ctrl+C to stop", flush=True)

        while running:
            sample = policy.RoutePolicyUpdate()
            info = fastdds.SampleInfo()

            # DDS ReturnCode OK is zero. Lifecycle samples have no valid data.
            if reader.take_next_sample(sample, info) != 0:
                time.sleep(0.05)
                continue
            if not info.valid_data:
                continue

            key = (sample.customer_asn(), sample.provider_asn())
            previous = cache.get(key)
            if previous is not None and sample.version() <= previous["version"]:
                print(json.dumps({
                    "event": "ignored_old_or_duplicate",
                    "customer_asn": key[0],
                    "provider_asn": key[1],
                    "version": sample.version(),
                }), flush=True)
                continue

            record = {
                "customer_asn": key[0],
                "provider_asn": key[1],
                "authorized": sample.authorized(),
                "version": sample.version(),
            }
            cache[key] = record
            print(json.dumps({
                "event": "cache_updated",
                **record,
                "cache_entries": len(cache),
            }), flush=True)
finally:
    participant.delete_contained_entities()
    factory.delete_participant(participant)
    print("DDS resources closed", flush=True)