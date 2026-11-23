import json
import os
import time
from uuid import uuid4

import httpx
import numpy as np
from confluent_kafka import Consumer, Producer, TopicPartition

from apps.worker.main import GROUP
from ml.artifact import active, score
from packages.contracts.stream import INPUT, envelope, query
from packages.db.connect import engine
from packages.features.history import ReplayHistory
from simulator.dataset import fixture
from simulator.live import CREDENTIALS, URL, submit


def summary(run=None):
    with engine.connect() as conn:
        return dict(query(conn,"""SELECT count(*) accepted,count(d.id) decided,
          count(*) FILTER(WHERE d.id IS NULL AND q.id IS NULL) pending,count(*) FILTER(WHERE d.id IS NULL AND q.id IS NOT NULL) failed,
          count(*) FILTER(WHERE d.action='approve') approved,count(*) FILTER(WHERE d.action='review') reviewed,
          count(*) FILTER(WHERE d.action='block') blocked,count(c.id) cases,count(c.id) FILTER(WHERE c.status<>'resolved') open_cases,
          count(*) FILTER(WHERE d.id IS NOT NULL AND ((d.action='approve')<>(c.id IS NULL)
              OR (c.id IS NOT NULL AND ct.transaction_id IS NULL))) case_errors
          FROM transactions t LEFT JOIN decisions d ON d.transaction_id=t.id LEFT JOIN cases c ON c.opening_decision_id=d.id
          LEFT JOIN case_transactions ct ON ct.case_id=c.id AND ct.transaction_id=t.id AND ct.relation='opening'
          LEFT JOIN LATERAL (SELECT id FROM quarantine WHERE transaction_id=t.id AND resolved_at IS NULL LIMIT 1) q ON true
          WHERE CAST(:r AS uuid) IS NULL OR t.run_id=:r""",r=run).mappings().one())


def drain(run=None):
    for _ in range(180):
        counts = summary(run)
        with engine.connect() as conn:
            pending = query(conn,"""SELECT count(*) FROM outbox o JOIN events e USING(event_id)
                LEFT JOIN transactions t ON t.id=e.transaction_id WHERE o.published_at IS NULL AND
                (CAST(:r AS uuid) IS NULL OR t.run_id=:r)""",r=run).scalar_one()
        if not pending and not counts["pending"]:
            if counts["failed"] or counts["case_errors"] or counts["accepted"]!=counts["decided"]:
                raise RuntimeError(f"Reconciliation failed: {counts}")
            return counts
        time.sleep(1)
    raise RuntimeError(f"Drain timed out: {counts}, outbox={pending}")


def replay(event):
    deliveries = []
    producer = Producer({"bootstrap.servers":"kafka:9092","acks":"all"})
    producer.produce(INPUT,key=event["account_id"],value=json.dumps(envelope(event)),
                     on_delivery=lambda err,msg:deliveries.append((err,msg.partition(),msg.offset())))
    producer.flush(10)
    if not deliveries or deliveries[0][0]:
        raise RuntimeError("Replay publish failed")
    _,part,offset = deliveries[0]
    probe = Consumer({"bootstrap.servers":"kafka:9092","group.id":GROUP,"enable.auto.commit":False})
    try:
        for _ in range(60):
            if probe.committed([TopicPartition(INPUT,part)],timeout=5)[0].offset>offset:
                print(f"Scoring group committed beyond replay partition={part} offset={offset}")
                return
            time.sleep(0.5)
        raise RuntimeError("Replay offset was not committed")
    finally:
        probe.close()


def check_pipeline():
    print("Before fixture:",drain())
    seed = uuid4().int % (2**31)
    records = list(fixture(seed))
    clock, first, _ = records[0]
    block_candidate = first.model_copy(update=dict(transaction_id=uuid4(),account_id="acct_"+uuid4().hex,
        merchant_country="GB",amount_minor=100000,channel="web",event_time=clock.replace(hour=0)))
    records.append((clock,block_candidate,{}))
    run = submit(records,seed,"fixture-v1")
    before = drain(run)
    history = ReplayHistory()
    registry,model = active()
    with engine.connect() as conn:
        for clock,p,_ in records:
            accepted,vector = history.accept(p,clock)
            row = query(conn,"""SELECT t.account_seq,t.accepted_at,d.score,d.action,s.values,e.bias,e.raw_margin,
                ARRAY(SELECT signed_value FROM contributions WHERE decision_id=d.id ORDER BY feature_index) contributions
                FROM transactions t JOIN decisions d ON d.transaction_id=t.id JOIN feature_snapshots s ON s.id=d.snapshot_id
                JOIN explanations e ON e.decision_id=d.id WHERE t.id=:tx""",tx=p.transaction_id).mappings().one()
            assert (row["account_seq"],row["accepted_at"])==(accepted.seq,accepted.accepted_at)
            np.testing.assert_allclose(row["values"],vector,rtol=0,atol=1e-6)
            scores,acts,contrib,_ = score(model,[vector],(registry["review_threshold"],registry["block_threshold"]))
            assert abs(row["score"]-scores[0])<=1e-6 and row["action"]==("approve","review","block")[acts[0]]
            np.testing.assert_allclose([*row["contributions"],row["bias"]],contrib[0],rtol=0,atol=1e-6)
            assert abs(sum(row["contributions"])+row["bias"]-row["raw_margin"])<=1e-4
        event = query(conn,"SELECT * FROM events WHERE transaction_id=:tx AND type=:t",tx=p.transaction_id,t=INPUT).mappings().one()
        priority = query(conn,"""SELECT c.priority FROM cases c JOIN decisions d ON d.id=c.opening_decision_id
                         WHERE d.transaction_id=:tx AND d.action='block'""",tx=block_candidate.transaction_id).scalar_one()
        assert priority=="high"
    replay(event)
    assert drain(run)==before
    p = records[0][1]
    body = p.model_dump(mode="json")
    with httpx.Client(base_url=URL) as client:
        auth = {"Authorization":"Bearer "+os.environ["SIMULATOR_TOKEN"]}
        assert client.post("/v1/transactions",json=body,headers=auth).status_code==202
        assert client.post("/v1/transactions",json={**body,"amount_minor":1},headers=auth).status_code==409
        assert client.post("/v1/transactions",json={**body,"label":1},headers=auth).status_code==422
        assert client.post("/v1/transactions",json=body).status_code==401
        detail = client.get(f"/v1/transactions/{p.transaction_id}",auth=("viewer",json.loads(CREDENTIALS.read_bytes())["viewer"]))
        detail.raise_for_status()
        assert detail.json()["state"]=="scored"
    print("PASS live equal-time parity, signed evidence, receipt replay/conflict and Kafka replay:",before)
    print("All runs:",drain())
