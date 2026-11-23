import hashlib
import os
import secrets
import time
from collections import deque
from datetime import datetime
from uuid import UUID, uuid4

from apps.api import access, workspace

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sqlalchemy.exc import SQLAlchemyError

from packages.contracts.payment import PaymentRequestV1
from packages.contracts.stream import INPUT, emit, query
from packages.db.connect import engine

from packages import metrics

app = FastAPI()

@app.on_event("startup")
def start_metrics():
    metrics.serve()
requests = deque()
app.include_router(access.router)
app.include_router(workspace.router)


def error(status, code, message):
    return JSONResponse(dict(code=code,message=message,request_id=str(uuid4())),status_code=status)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    return error(422,"invalid_request","Invalid payment or identifier")


@app.exception_handler(SQLAlchemyError)
async def unavailable(request, exc):
    return error(503,"storage_unavailable","Storage temporarily unavailable")


@app.middleware("http")
async def limit(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/v1/transactions":
        auth = request.headers.get("authorization","")
        if not secrets.compare_digest(auth.encode(),("Bearer "+os.environ["SIMULATOR_TOKEN"]).encode()):
            return error(401,"unauthorized","Simulator credential required")
        now = time.monotonic()
        while requests and requests[0] <= now-1:
            requests.popleft()
        if len(requests) >= 200:
            return error(429,"rate_limit","Retry in one second")
        requests.append(now)
    if request.method in {"POST","PATCH"}:
        body = bytearray()
        async for chunk in request.stream():
            if len(body)+len(chunk)>16384:
                return error(413,"body_limit","Payment exceeds 16 KiB")
            body.extend(chunk)
        request._body = bytes(body)
    return await call_next(request)


@app.get("/health/live")
def live():
    return {"live":True}


@app.get("/health/ready")
def ready():
    with engine.connect() as conn:
        query(conn,"SELECT t.id FROM transactions t JOIN outbox o ON false LIMIT 0")
    return {"ready":True}


@app.post("/v1/transactions",status_code=202)
def ingest(payment: PaymentRequestV1, x_simulated_at: datetime | None = Header(None)):
    p = payment
    digest = hashlib.sha256(p.canonical().encode()).hexdigest()
    receipt = dict(transaction_id=str(p.transaction_id),status_url=f"/v1/transactions/{p.transaction_id}")
    with engine.begin() as conn:
        query(conn,"SELECT pg_advisory_xact_lock(hashtextextended(:id,0))",id=str(p.transaction_id))
        existing = query(conn,"SELECT request_hash FROM transactions WHERE id=:id",id=p.transaction_id).scalar()
        if existing:
            if existing != digest:
                raise HTTPException(409,"Transaction ID already has a different payment")
            metrics.duplicates.inc()
            return receipt
        mode = query(conn,"SELECT clock_mode FROM runs WHERE id=:id",id=p.run_id).scalar()
        if mode is None or (x_simulated_at and (mode!="logical" or x_simulated_at.utcoffset() is None)):
            raise HTTPException(422,"Unknown run or invalid simulator clock")
        query(conn,"INSERT INTO accounts(id,run_id) VALUES(:a,:r) ON CONFLICT DO NOTHING",a=p.account_id,r=p.run_id)
        state = query(conn,"SELECT * FROM accounts WHERE id=:a FOR UPDATE",a=p.account_id).mappings().one()
        if state["run_id"] != p.run_id:
            raise HTTPException(409,"Account belongs to another run")
        received = query(conn,"SELECT clock_timestamp()").scalar_one()
        clock = x_simulated_at or received
        accepted = max(clock,state["last_accepted_at"] or clock)
        seq = state["next_seq"]
        query(conn,"""INSERT INTO transactions(id,run_id,account_id,account_seq,merchant_id,device_id,channel,
              amount_minor,currency,account_country,merchant_country,event_time,received_at,accepted_at,request_hash)
              VALUES(:transaction_id,:run_id,:account_id,:seq,:merchant_id,:device_id,:channel,:amount_minor,:currency,
              :account_country,:merchant_country,:event_time,:received,:accepted,:digest)""",**p.model_dump(),
              seq=seq,received=received,accepted=accepted,digest=digest)
        query(conn,"UPDATE accounts SET next_seq=next_seq+1,last_accepted_at=:ts WHERE id=:a",ts=accepted,a=p.account_id)
        emit(conn,INPUT,p.transaction_id,p.account_id,seq,p.transaction_id,{"transaction_id":str(p.transaction_id)})
    metrics.accepted.inc()
    return receipt


@app.get("/v1/transactions/{tx}")
def detail(tx: UUID, reader=Depends(access.detail_reader)):
    with engine.connect() as conn:
        row = query(conn,"""SELECT t.id AS transaction_id,t.received_at,t.amount_minor,t.currency,t.account_seq,t.account_id,t.merchant_id,t.device_id,t.channel,t.event_time,t.run_id,
            d.id AS decision_id,d.action,d.score,d.model_version_id,m.version AS model_version,m.review_threshold,m.block_threshold,
            s.values AS features,f.ordered_manifest AS manifest,c.id AS case_id, e.bias,e.raw_margin,
            CASE WHEN d.id IS NOT NULL THEN 'scored' WHEN EXISTS(SELECT 1 FROM quarantine q
                WHERE q.transaction_id=t.id AND resolved_at IS NULL) THEN 'failed' ELSE 'pending' END AS state
            FROM transactions t LEFT JOIN decisions d ON d.transaction_id=t.id LEFT JOIN model_versions m ON m.id=d.model_version_id
            LEFT JOIN feature_snapshots s ON s.id=d.snapshot_id LEFT JOIN cases c ON c.opening_decision_id=d.id
            LEFT JOIN feature_schemas f ON f.id=s.schema_id LEFT JOIN explanations e ON e.decision_id=d.id WHERE t.id=:tx""",tx=tx).mappings().one_or_none()
        if row is None:
            raise HTTPException(404,"Unknown transaction")
        return dict(row,contributions=query(conn,"SELECT feature_name,feature_value,signed_value FROM contributions WHERE decision_id=:d ORDER BY feature_index",d=row["decision_id"]).mappings().all())
