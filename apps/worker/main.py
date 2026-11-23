import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import redis
from confluent_kafka import Consumer, KafkaException, TopicPartition
from sqlalchemy.exc import SQLAlchemyError

from ml.artifact import active, predict_evidence
from packages.contracts.payment import PaymentRequestV1
from packages.contracts.stream import DEAD, INPUT, OUTPUT, emit, envelope, query
from packages.db.connect import engine
from packages import metrics
from packages.recovery_point import crash_if_armed
from packages.features.history import Accepted, DAY, vector

GROUP = "payment-scoring-v1"
cache = redis.Redis(host="redis",username="worker",password=os.getenv("REDIS_PASSWORD"),
                    socket_timeout=0.4,socket_connect_timeout=0.4,decode_responses=True)


class InvalidEnvelope(ValueError):
    pass


class Gap(Exception):
    pass


def accepted(row):
    fields = {k:row[k] for k in PaymentRequestV1.model_fields if k!="transaction_id"}
    return Accepted(PaymentRequestV1(transaction_id=row["id"],**fields),row["accepted_at"],row["account_seq"])


def history(conn, current, watermark):
    key = "history:"+current.payment.account_id
    cutoff = current.accepted_at-DAY
    try:
        cached = json.loads(cache.get(key) or "null")
        if cached and cached["seq"]==watermark:
            return [Accepted(PaymentRequestV1.model_validate(p),datetime.fromisoformat(ts),seq)
                    for p,ts,seq in cached["rows"] if datetime.fromisoformat(ts)>=cutoff]
    except (redis.RedisError,ValueError,KeyError,TypeError):
        pass
    metrics.fallbacks.inc()
    rows = query(conn,"""SELECT * FROM transactions WHERE account_id=:a AND account_seq<=:w
                 AND accepted_at>=:cutoff ORDER BY account_seq""",a=current.payment.account_id,w=watermark,cutoff=cutoff)
    return [accepted(r) for r in rows.mappings()]


def update_cache(current, rows, watermark):
    key = "history:"+current.payment.account_id
    try:
        with cache.pipeline() as pipe:
            pipe.watch(key)
            old = json.loads(pipe.get(key) or "null")
            if old and old["seq"]>watermark:
                return
            value = dict(seq=current.seq,rows=[(r.payment.model_dump(mode="json"),r.accepted_at.isoformat(),r.seq)
                                                for r in [*rows,current]])
            pipe.multi()
            pipe.set(key,json.dumps(value),ex=86400)
            pipe.execute()
    except (redis.RedisError,ValueError,KeyError,TypeError):
        pass


def persist(conn, current, received, values, model_row, model, correlation):
    tx = current.payment.transaction_id
    scores, actions, contrib, _, margins = predict_evidence(model,[values],(model_row["review_threshold"],model_row["block_threshold"]))
    action = ("approve","review","block")[actions[0]]
    snap, decision = uuid4(), uuid4()
    query(conn,"""INSERT INTO feature_snapshots(id,transaction_id,schema_id,history_through_seq,values)
        VALUES(:id,:tx,:schema,:seq,CAST(:v AS jsonb))""",id=snap,tx=tx,schema=model_row["schema_id"],
        seq=current.seq-1,v=json.dumps(values))
    query(conn,"""INSERT INTO decisions(id,transaction_id,model_version_id,snapshot_id,schema_id,score,action,latency_ms,policy_version)
        VALUES(:d,:tx,:m,:s,:schema,:score,:action,greatest(0,extract(epoch FROM clock_timestamp()-CAST(:received AS timestamptz))*1000)::bigint,'threshold-v1')""",
        d=decision,tx=tx,m=model_row["id"],s=snap,schema=model_row["schema_id"],score=float(scores[0]),action=action,received=received)
    margin = margins[0]
    query(conn,"""INSERT INTO explanations(decision_id,method,output_space,bias,raw_margin)
        VALUES(:d,'pred_contribs','log_odds',:bias,:margin)""",d=decision,bias=float(contrib[0,-1]),margin=float(margin))
    query(conn,"""INSERT INTO contributions(decision_id,feature_index,feature_name,feature_value,signed_value)
        SELECT :d,ord-1,name,value,contribution FROM unnest(CAST(:names AS text[]),
        CAST(:values AS float8[]),CAST(:contrib AS float8[])) WITH ORDINALITY AS f(name,value,contribution,ord)""",
        d=decision,names=model.feature_names,values=list(map(float,values)),contrib=contrib[0,:-1].tolist())
    case = None
    if action!="approve":
        case = uuid4()
        query(conn,"INSERT INTO cases(id,opening_decision_id,priority) VALUES(:c,:d,:p)",
              c=case,d=decision,p="high" if action=="block" else "normal")
        query(conn,"INSERT INTO case_transactions(case_id,transaction_id,relation) VALUES(:c,:tx,'opening')",c=case,tx=tx)
        query(conn,"""INSERT INTO case_events(id,case_id,kind,before_after,correlation_id)
              VALUES(:id,:c,'opened','{"status":"open"}',:corr)""",id=uuid4(),c=case,corr=correlation)
    emit(conn,OUTPUT,tx,current.payment.account_id,current.seq,correlation,dict(decision_id=str(decision),
         action=action,model_version_id=str(model_row["id"]),case_id=str(case) if case else None))


    return action


def process(data, key, model_row, model):
    start = time.monotonic()
    with engine.begin() as conn:
        try:
            eid = UUID(data["event_id"])
        except (TypeError,KeyError,ValueError,AttributeError) as exc:
            raise InvalidEnvelope from exc
        event = query(conn,"SELECT * FROM events WHERE event_id=:id AND type=:t",id=eid,t=INPUT).mappings().one_or_none()
        if (not event or event["schema_version"]!=1 or key!=event["account_id"].encode()
                or json.dumps(data,sort_keys=True)!=json.dumps(envelope(event),sort_keys=True)):
            raise InvalidEnvelope
        row = query(conn,"SELECT * FROM transactions WHERE id=:id",id=event["transaction_id"]).mappings().one()
        current = accepted(row)
        watermark = query(conn,"SELECT last_scored_seq FROM accounts WHERE id=:id FOR UPDATE",
                          id=current.payment.account_id).scalar_one()
        if query(conn,"SELECT id FROM decisions WHERE transaction_id=:id",id=row["id"]).scalar():
            metrics.duplicates.inc()
            return
        if current.seq!=watermark+1:
            raise Gap(current.payment.account_id,watermark+1)
        if query(conn,"SELECT id FROM quarantine WHERE transaction_id=:id AND resolved_at IS NULL LIMIT 1",id=row["id"]).scalar():
            raise Gap(current.payment.account_id,current.seq)
        prior = history(conn,current,watermark)
        action = persist(conn,current,row["received_at"],vector(current,prior),model_row,model,event["correlation_id"])
        query(conn,"UPDATE accounts SET last_scored_seq=:seq WHERE id=:a",seq=current.seq,a=current.payment.account_id)
    crash_if_armed("worker-after-commit")
    metrics.decisions.labels(action).inc()
    metrics.latency.observe(time.time()-row["received_at"].timestamp())
    metrics.scoring.observe(time.monotonic()-start)
    update_cache(current,prior,watermark)


def quarantine(msg,data):
    try:
        tx = UUID(data["transaction_id"])
    except (TypeError,ValueError,KeyError,AttributeError):
        tx = None
    with engine.begin() as conn:
        tx = query(conn,"SELECT id FROM transactions WHERE id=:id",id=tx).scalar()
        info = dict(topic=msg.topic(),partition=msg.partition(),offset=msg.offset(),reason_code="invalid_envelope")
        qid = query(conn,"""INSERT INTO quarantine(id,topic,partition,"offset",reason_code,transaction_id)
              VALUES(:id,:topic,:partition,:offset,:reason_code,:tx) ON CONFLICT DO NOTHING RETURNING id""",
              id=uuid4(),tx=tx,**info).scalar()
        if qid:
            emit(conn,DEAD,tx,None,None,qid,info)

    if qid:
        metrics.dlq.inc()


def run():
    metrics.serve()
    model_row, model = active()
    def observe_lag(value):
        partitions = json.loads(value)["topics"].get(INPUT,{}).get("partitions",{})
        for part, state in partitions.items():
            if int(part)>=0 and state.get("consumer_lag",-1)>=0:
                metrics.lag.labels(part).set(state["consumer_lag"])

    consumer = Consumer({"bootstrap.servers":"kafka:9092","group.id":GROUP,"auto.offset.reset":"earliest",
                         "stats_cb":observe_lag,"statistics.interval.ms":2000,"fetch.wait.max.ms":10,"session.timeout.ms":10000,"auto.commit.interval.ms":200,"enable.auto.commit":True,"enable.auto.offset.store":False})
    metrics.ready.set(1)
    paused = {}
    def revoke(consumer, partitions):
        paused.clear()
    consumer.subscribe([INPUT],on_revoke=revoke)
    last_probe = 0
    while True:
        now = time.monotonic()
        for part, at in list(paused.items()):
            if now>=at:
                consumer.resume([TopicPartition(INPUT,part)])
                del paused[part]
        msg = consumer.poll(0.25)
        if now-last_probe>5:
            try:
                consumer.list_topics(INPUT,timeout=3)
            except KafkaException:
                logging.warning("Broker metadata unavailable")
                if msg is None or msg.error():
                    continue
                raise
            with engine.connect() as conn:
                query(conn,"SELECT 1")
            Path("/tmp/ready").touch()
            last_probe = now
        if msg is None or msg.error():
            continue
        data = None
        try:
            try:
                data = json.loads(msg.value() or b"")
                process(data,msg.key(),model_row,model)
            except (InvalidEnvelope,json.JSONDecodeError,UnicodeDecodeError):
                quarantine(msg,data)
            consumer.store_offsets(message=msg)  # Only completed SQL transactions enter the commit watermark.
        except (Gap, SQLAlchemyError) as exc:
            metrics.retries.labels("worker").inc()
            logging.warning("Pausing partition %d offset %d: %s",msg.partition(),msg.offset(),type(exc).__name__)
            tp = TopicPartition(INPUT,msg.partition(),msg.offset())
            consumer.pause([tp])
            consumer.seek(tp)
            paused[msg.partition()] = time.monotonic()+2
            if isinstance(exc,Gap):
                account, seq = exc.args
                with engine.connect() as conn:
                    missing = query(conn,"SELECT * FROM events WHERE type=:t AND account_id=:a AND account_seq=:s LIMIT 1",
                                    t=INPUT,a=account,s=seq).mappings().one_or_none()
                if missing:
                    try:
                        process(envelope(missing),account.encode(),model_row,model)
                    except Gap:
                        pass


if __name__=="__main__":
    run()
