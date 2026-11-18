"""Isolated DDS 175 lab adapter to project-owned ONOS intent keys."""
import argparse
import json
import signal
import threading
import time
import fastdds
import HelloWorld
import onos_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['receiver', 'sender'])
    parser.add_argument('--action', choices=['connect', 'disconnect'], default='connect')
    args = parser.parse_args()
    running = threading.Event()
    running.set()
    signal.signal(signal.SIGINT, lambda *a: running.clear())
    signal.signal(signal.SIGTERM, lambda *a: running.clear())
    factory = fastdds.DomainParticipantFactory.get_instance()
    pq = fastdds.DomainParticipantQos()
    factory.get_default_participant_qos(pq)
    participant = factory.create_participant(175, pq)
    if participant is None: raise RuntimeError('DDS participant creation failed')
    try:
        data_type = HelloWorld.HelloWorldPubSubType()
        data_type.set_name('RONOOnosLabEnvelope')
        support = fastdds.TypeSupport(data_type)
        participant.register_type(support)
        tq = fastdds.TopicQos()
        participant.get_default_topic_qos(tq)
        topic = participant.create_topic('RONO_ONOS_LabIntent', data_type.get_name(), tq)
        if topic is None: raise RuntimeError('DDS topic creation failed')
        def qos(q):
            q.reliability().kind = fastdds.RELIABLE_RELIABILITY_QOS
            q.durability().kind = fastdds.VOLATILE_DURABILITY_QOS
            q.history().kind = fastdds.KEEP_LAST_HISTORY_QOS
            q.history().depth = 20
        if args.role == 'receiver':
            sq = fastdds.SubscriberQos();participant.get_default_subscriber_qos(sq)
            subscriber = participant.create_subscriber(sq)
            rq = fastdds.DataReaderQos();subscriber.get_default_datareader_qos(rq);qos(rq)
            reader = subscriber.create_datareader(topic, rq)
            if reader is None: raise RuntimeError('DDS reader creation failed')
            print(json.dumps({'event':'onos_dds_ready','domain':175}), flush=True)
            seen = set()
            while running.is_set():
                sample = HelloWorld.HelloWorld();info = fastdds.SampleInfo()
                if reader.take_next_sample(sample, info) != 0:
                    time.sleep(.02);continue
                if not info.valid_data: continue
                try:
                    message = json.loads(sample.message())
                    if set(message) != {'schema','action','request_id'} or message['schema'] != 1:
                        raise ValueError('Invalid envelope')
                    rid = message['request_id']
                    if not isinstance(rid,str) or len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid):
                        raise ValueError('Invalid request ID')
                    if rid in seen: continue
                    result = onos_client.apply(message['action'])
                    seen.add(rid)
                    if len(seen)>256: seen={rid}
                    print(json.dumps({'event':'onos_intent_applied','request_id':rid,**result}),flush=True)
                except Exception as error:
                    print(json.dumps({'event':'onos_intent_error','error':str(error)}),flush=True)
        else:
            matched = threading.Event()
            class Listener(fastdds.DataWriterListener):
                def __init__(self): super().__init__()
                def on_publication_matched(self, writer, info):
                    if info.current_count_change>0: matched.set()
            pubq=fastdds.PublisherQos();participant.get_default_publisher_qos(pubq)
            publisher=participant.create_publisher(pubq)
            wq=fastdds.DataWriterQos();publisher.get_default_datawriter_qos(wq);qos(wq)
            listener=Listener();writer=publisher.create_datawriter(topic,wq,listener)
            if writer is None or not matched.wait(15): raise RuntimeError('DDS reader discovery failed')
            import uuid
            message={'schema':1,'request_id':uuid.uuid4().hex,'action':args.action}
            sample=HelloWorld.HelloWorld();sample.index(0);sample.message(json.dumps(message))
            if writer.write(sample)!=0: raise RuntimeError('DDS publication failed')
            print(json.dumps({'event':'onos_dds_published',**message}),flush=True)
            time.sleep(2)
    finally:
        participant.delete_contained_entities();factory.delete_participant(participant)

if __name__=='__main__': main()
