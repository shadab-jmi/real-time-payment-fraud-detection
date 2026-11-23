import hashlib
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.db.connect import upgrade


def require(condition, message):
    if not condition:
        raise RuntimeError(message)
    print(f"PASS: {message}")


def reject(conn, state, statement, params, constraint=None):
    try:
        with conn.begin_nested():
            conn.execute(text(statement), params)
    except DBAPIError as exc:
        if exc.orig.sqlstate != state or (constraint and exc.orig.diag.constraint_name != constraint):
            raise
        print(f"PASS: PostgreSQL rejected {constraint or statement.split()[0]} ({state})")
    else:
        raise RuntimeError(f"Expected SQLSTATE {state}")


RECEIPT = """
INSERT INTO transactions(id,run_id,account_id,account_seq,merchant_id,device_id,channel,amount_minor,
    currency,account_country,merchant_country,event_time,accepted_at,request_hash)
VALUES (:tx,:run,:account,:seq,'mer_fixture',:device,'web',149900,'INR','IN','IN',now(),now(),repeat('a',64))
"""


def receipt(conn):
    p = dict(run=uuid4(), account="acct_" + uuid4().hex, tx=uuid4(), seq=1, device="dev_fixture")
    conn.execute(text("INSERT INTO runs (id,seed,generator_version,clock_mode) VALUES (:run,42,'storage-fixture','logical')"), p)
    conn.execute(text("INSERT INTO accounts (id,run_id) VALUES (:account,:run)"), p)
    conn.execute(text(RECEIPT), p)
    return p


def check_upgrade(conn):
    require(conn.exec_driver_sql("SELECT to_regclass('public.runs') IS NULL").scalar_one(),
            "upgrade fixture uses an empty database")
    upgrade(conn, "0001_receipts")
    p = receipt(conn)
    event = uuid4()
    conn.execute(text("""INSERT INTO events(event_id,transaction_id,account_id,account_seq,type,schema_version,
                        correlation_id,payload) VALUES (:event,:tx,:account,1,'payments.received.v1',1,:run,'{}')"""),
                 {**p, "event": event})
    conn.execute(text("INSERT INTO outbox(event_id,topic,key) VALUES (:event,'payments.received.v1',:account)"),
                 {**p, "event": event})
    require(upgrade(conn) == "0004_simulations", "populated previous revision upgraded to head")
    row = conn.execute(text("""SELECT t.amount_minor, o.published_at FROM transactions t
                            JOIN events e ON e.transaction_id=t.id JOIN outbox o USING(event_id)
                            WHERE t.id=:tx AND e.event_id=:event"""), {**p, "event": event}).one()
    require(tuple(row) == (149900, None), "original receipt and pending outbox event survived upgrade")


def check_storage(conn):
    require(conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0004_simulations",
            "expected migration head")
    p = receipt(conn)
    reject(conn, "23505", RECEIPT, {**p, "tx": uuid4()}, "uq_account_sequence")
    reject(conn, "23503", "INSERT INTO accounts(id,run_id) VALUES ('acct_orphan',:run)",
           {"run": uuid4()}, "accounts_run_id_fkey")
    reject(conn, "23514", "UPDATE transactions SET amount_minor=1 WHERE id=:tx", p)
    p.update(tx2=uuid4(), schema="schema_" + uuid4().hex, model=uuid4(), snapshot=uuid4(),
             decision=uuid4(), case=uuid4(), user=uuid4(), note=uuid4())
    conn.execute(text(RECEIPT), {**p, "tx": p["tx2"], "seq": 2, "device": None})
    manifest = [{"name": "example", "type": "number", "default": 0, "unit": "count",
                 "transformation": "identity", "fixture_id": p["schema"]}]
    p["manifest"] = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    p["digest"] = hashlib.sha256(p["manifest"].encode()).hexdigest()
    statements = [
        """INSERT INTO feature_schemas(id,ordered_manifest,digest)
           VALUES (:schema,CAST(:manifest AS jsonb),:digest)""",
        """INSERT INTO model_versions(id,version,schema_id,artifact_path,artifact_sha256,metrics,lineage,
           review_threshold,block_threshold) VALUES (:model,:schema,:schema,'fixture.json',repeat('b',64),'{}','{}',0.5,0.9)""",
        """INSERT INTO feature_snapshots(id,transaction_id,schema_id,history_through_seq,values)
           VALUES (:snapshot,:tx,:schema,0,'[1]')""",
        """INSERT INTO decisions(id,transaction_id,model_version_id,snapshot_id,schema_id,score,action,latency_ms,policy_version)
           VALUES (:decision,:tx,:model,:snapshot,:schema,0.6,'review',1,'fixture')""",
        """INSERT INTO explanations(decision_id,method,output_space,bias,raw_margin)
           VALUES (:decision,'fixture','log_odds',0,0.4)""",
        """INSERT INTO contributions VALUES (:decision,0,'example',1,0.4)""",
        """INSERT INTO cases(id,opening_decision_id,priority) VALUES (:case,:decision,'normal')""",
        """INSERT INTO case_transactions(case_id,transaction_id,relation) VALUES (:case,:tx,'opening'),(:case,:tx2,'related')""",
        """INSERT INTO users(id,username,password_hash,role,disabled) VALUES (:user,:schema,'disabled-fixture','analyst',true)""",
        """INSERT INTO case_notes(id,case_id,author_id,body) VALUES (:note,:case,:user,'Fixture note')""",
    ]
    for sql in statements:
        conn.execute(text(sql), p)
    # This view must retain related transactions that are still pending.
    rows = conn.execute(text("""SELECT transaction_id, decision_id, snapshot_id, model_version_id
                             FROM v_case_evidence_v1 WHERE case_id=:case ORDER BY relation"""), p).all()
    require([tuple(r) for r in rows] == [(p["tx"], p["decision"], p["snapshot"], p["model"]),
                                         (p["tx2"], None, None, None)], "case evidence resolves both transactions")
    counts = conn.execute(text("""SELECT
        (SELECT count(*) FROM v_entity_edges_v1 WHERE transaction_id IN (:tx,:tx2)) AS edges,
        (SELECT count(*) FROM v_case_notes_v1 WHERE note_id=:note AND case_id=:case) AS notes,
        (SELECT count(*) FROM v_decision_contributions_v1 WHERE decision_id=:decision AND feature_index=0) AS contributions"""), p).one()
    require(tuple(counts) == (5, 1, 1), f"edges={counts.edges}, notes={counts.notes}, contributions={counts.contributions}")
    privilege_rows = conn.exec_driver_sql("""
        SELECT c.relname, has_table_privilege('fraud_reader',c.oid,'SELECT') AS can_read,
               has_table_privilege('fraud_reader',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('public','evaluation') AND c.relkind IN ('r','v')
    """).all()
    expected = {'v_case_evidence_v1','v_entity_edges_v1','v_case_notes_v1','v_decision_contributions_v1'}
    require({r.relname for r in privilege_rows if r.can_read} == expected
            and not any(r.can_write for r in privilege_rows), "reader can select exactly four evidence views and cannot write")
