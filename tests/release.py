import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from confluent_kafka import Producer

from packages.contracts.stream import INPUT, envelope, query
from packages.db.connect import engine
from simulator.dataset import fixture
from simulator.live import URL, submit

DIR=Path("/evidence")
STEPS=("fresh_install","crash_replay","worker_backlog","redis_fallback","broker_backlog","sql_503",
       "quarantine_repair","reader_continuity","offline_restart")


def reader_fixture(case=None):
    with engine.connect() as c:
        if case is None:
            case=query(c,"""SELECT n.case_id FROM case_notes n JOIN v_case_evidence_v1 v ON v.case_id=n.case_id WHERE EXISTS (SELECT 1 FROM transactions t WHERE t.device_id=v.device_id AND t.id<>v.transaction_id) ORDER BY n.created_at DESC LIMIT 1""").scalar_one()
    with psycopg.connect(user="fraud_reader",password=os.environ["READER_PASSWORD"],row_factory=dict_row) as db:
        def rows(sql, *p):
            return db.execute(sql,p or None).fetchall()
        evidence=rows("SELECT * FROM v_case_evidence_v1 WHERE case_id=%s ORDER BY transaction_id",case)
        notes=rows("SELECT * FROM v_case_notes_v1 WHERE case_id=%s ORDER BY note_id",case)
        assert evidence and notes
        tx=evidence[0]["transaction_id"]
        contrib=rows("SELECT * FROM v_decision_contributions_v1 WHERE transaction_id=%s ORDER BY feature_index",tx)
        edges=rows("SELECT * FROM v_entity_edges_v1 WHERE transaction_id=%s ORDER BY entity_type",tx)
        related=rows("""SELECT DISTINCT b.transaction_id FROM v_entity_edges_v1 a JOIN v_entity_edges_v1 b
            ON a.entity_type=b.entity_type AND a.entity_id=b.entity_id WHERE a.transaction_id=%s
            AND a.entity_type='device' AND b.transaction_id<>a.transaction_id ORDER BY b.transaction_id""",tx)
        assert len(contrib)==12 and edges and related
        permissions=rows("""SELECT relname,has_table_privilege(current_user,oid,'INSERT,UPDATE,DELETE,TRUNCATE') can_write,
            has_table_privilege(current_user,oid,'SELECT') can_read FROM pg_class
            WHERE relnamespace IN ('public'::regnamespace,'evaluation'::regnamespace) AND relkind IN ('r','v')""")
        assert not any(r["can_write"] for r in permissions)
        assert {r["relname"] for r in permissions if r["can_read"]}=={
            "v_case_evidence_v1","v_case_notes_v1","v_decision_contributions_v1","v_entity_edges_v1"}
        schema=rows("""SELECT table_name,column_name,data_type FROM information_schema.columns
                       WHERE table_schema='public' AND table_name LIKE 'v_%' ORDER BY table_name,ordinal_position""")
        other=rows("SELECT note_id FROM v_case_notes_v1 WHERE case_id<>%s LIMIT 1",case)
        if other:
            assert not rows("SELECT note_id FROM v_case_notes_v1 WHERE case_id=%s AND note_id=%s",case,other[0]["note_id"])
    return dict(case_id=str(case),evidence=evidence,notes=notes,contributions=contrib,edges=edges,related=related,schema=schema)


def load_fixture():
    records=list(fixture(98765))
    return records


def malformed():
    # Relay is stopped by the host. Receipt exists, but the legitimate event has not yet reached Kafka.
    clock,p,truth=load_fixture()[0]
    submit([(clock,p,truth)],98765,"fixture-v1")
    prod=Producer({"bootstrap.servers":"kafka:9092","acks":"all"})
    with engine.connect() as c:
        row=query(c,"SELECT * FROM events WHERE type=:t AND transaction_id=:tx",t=INPUT,tx=p.transaction_id).mappings().one()
    data=envelope(row)
    data["schema_version"]=999
    result=[]
    prod.produce(INPUT,key=p.account_id,value=json.dumps(data),on_delivery=lambda err,msg:result.append(err))
    prod.flush(10)
    assert result==[None]


def repair(qid):
    # Clear the failed state and schedule the original canonical input for re-publication in one commit.
    from packages.contracts.stream import emit
    with engine.begin() as c:
        q=query(c,"SELECT * FROM quarantine WHERE id=:id FOR UPDATE",id=qid).mappings().one()
        if q["resolved_at"]:
            return
        event=query(c,"SELECT * FROM events WHERE type=:t AND transaction_id=:tx",t=INPUT,tx=q["transaction_id"]).mappings().one()
        query(c,"UPDATE quarantine SET resolved_at=now() WHERE id=:id",id=qid)
        query(c,"UPDATE outbox SET published_at=NULL,next_attempt_at=now() WHERE event_id=:id",id=event["event_id"])
        # Immutable SQL audit entry, no additional Kafka topic needed.
        query(c,"""INSERT INTO events(event_id,transaction_id,account_id,account_seq,type,schema_version,correlation_id,payload)
            VALUES(:id,:tx,:a,:seq,'fraud.quarantine.repaired.v1',1,:corr,CAST(:payload AS jsonb))""",
            id=uuid4(),tx=q["transaction_id"],a=event["account_id"],seq=event["account_seq"],
            corr=qid,payload=json.dumps({"quarantine_id":str(qid),"actor":"local-operator","retry_event_id":str(event["event_id"])}))


def check_recovery():
    data=json.loads((DIR/"recovery.json").read_text(encoding="utf-8-sig"))
    if not data["project"].startswith("fraud-drill-") or set(data["passed"])!=set(STEPS):
        raise RuntimeError("Incomplete host recovery evidence")
    print(json.dumps(data))


def sql_outage():
    _,p,_=next(fixture(98766))
    r=httpx.post(URL+"/v1/transactions",headers={"Authorization":"Bearer "+os.environ["SIMULATOR_TOKEN"]},
                 json=p.model_dump(mode="json"),timeout=20)
    assert r.status_code==503,r.status_code


def main():
    import sys
    cmd=sys.argv[1]
    if cmd=="malformed":
        malformed()
    elif cmd=="sql-outage":
        sql_outage()
    elif cmd=="reader":
        path=DIR/sys.argv[2]
        prior=json.loads(path.read_text()) if path.exists() else None
        result=json.loads(json.dumps(reader_fixture(prior["case_id"] if prior else None),default=str))
        if prior:
            assert prior==result,"Durable evidence changed"
        else:
            path.write_text(json.dumps(result,indent=2))
        print("PASS reader evidence, identity, stored checksum",result["case_id"])
    else:
        raise ValueError(cmd)


if __name__=="__main__":
    main()
