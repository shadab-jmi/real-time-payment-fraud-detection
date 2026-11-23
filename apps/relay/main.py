import json
import time
from pathlib import Path

from confluent_kafka import Producer

from packages.contracts.stream import INPUT, envelope, query
from packages.db.connect import engine
from packages import metrics
from packages.recovery_point import crash_if_armed


def publish(producer):
    with engine.begin() as conn:
        if not query(conn,"SELECT pg_try_advisory_xact_lock(715304)").scalar_one():
            return False
        event = query(conn,"""SELECT e.*,o.topic,o.key FROM outbox o JOIN events e USING(event_id)
            WHERE o.published_at IS NULL AND o.next_attempt_at<=now()
            AND (o.topic<>:input OR NOT EXISTS (SELECT 1 FROM outbox prev JOIN events p USING(event_id)
                WHERE prev.published_at IS NULL AND prev.topic=o.topic AND prev.key=o.key AND p.account_seq<e.account_seq))
            ORDER BY o.next_attempt_at,e.account_seq LIMIT 1""",input=INPUT).mappings().one_or_none()
        if event is None:
            return False
        results = []
        producer.produce(event["topic"],key=event["key"],value=json.dumps(envelope(event)),
                         on_delivery=lambda err,msg:results.append(err))
        producer.flush(6)
        if results != [None]:
            metrics.retries.labels("relay").inc()
            query(conn,"""UPDATE outbox SET attempts=attempts+1,last_error='broker_unavailable',
                next_attempt_at=now()+interval '1 second' WHERE event_id=:id""",id=event["event_id"])
        else:
            crash_if_armed("relay-after-ack")
            query(conn,"UPDATE outbox SET published_at=now(),attempts=attempts+1,last_error=NULL WHERE event_id=:id",id=event["event_id"])
        return True


if __name__ == "__main__":
    producer = Producer({"bootstrap.servers":"kafka:9092","acks":"all","enable.idempotence":True,"message.timeout.ms":5000})
    metrics.serve()
    sample_at = 0
    while True:
        if time.monotonic() >= sample_at:
            with engine.connect() as conn:
                n, seconds = query(conn,"""SELECT count(*),coalesce(extract(epoch FROM now()-min(e.occurred_at)),0)
                    FROM outbox o JOIN events e USING(event_id) WHERE o.published_at IS NULL""").one()
            metrics.pending.set(n)
            metrics.age.set(max(0,seconds))
            sample_at = time.monotonic()+2
        busy = publish(producer)
        Path("/tmp/ready").touch()
        if not busy:
            time.sleep(0.08)
