import json
from datetime import timezone
from uuid import uuid4

from sqlalchemy import text

INPUT = "payments.received.v1"
OUTPUT = "fraud.decided.v1"
DEAD = "fraud.deadletter.v1"


def query(conn, sql, **params):
    return conn.execute(text(sql), params)


def emit(conn, topic, tx, account, seq, correlation, payload):
    eid = uuid4()
    query(conn, """INSERT INTO events(event_id,transaction_id,account_id,account_seq,type,schema_version,correlation_id,payload)
          VALUES(:eid,:tx,:account,:seq,:topic,1,:correlation,CAST(:payload AS jsonb))""",
          eid=eid,tx=tx,account=account,seq=seq,topic=topic,correlation=correlation,payload=json.dumps(payload))
    query(conn, "INSERT INTO outbox(event_id,topic,key) VALUES(:eid,:topic,:key)",
          eid=eid,topic=topic,key=account or str(correlation))


def envelope(row):
    return {k:(str(row[k]) if row[k] is not None else None) for k in
            ("event_id","transaction_id","correlation_id","account_id","type")} | dict(
        schema_version=row["schema_version"],account_seq=row["account_seq"],payload=row["payload"],
        occurred_at=row["occurred_at"].astimezone(timezone.utc).isoformat().replace("+00:00","Z"))
