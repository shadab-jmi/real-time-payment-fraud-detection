from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import os
import secrets
import time
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field

from apps.api.workspace import STATE
from apps.api.access import digest, origin, session_user
from packages.contracts.stream import query
from packages.db.connect import engine
from simulator.generate import VERSION, generate
from simulator.live import showcase_records

router = APIRouter(prefix="/v1/simulations")
COOKIE = "simulation_owner"
COUNTS = """SELECT count(*) generated,count(d.id) processed,
    count(*) FILTER(WHERE action='approve') approved,count(*) FILTER(WHERE action='review') reviewed,
    count(*) FILTER(WHERE action='block') blocked FROM transactions t
    LEFT JOIN decisions d ON d.transaction_id=t.id WHERE t.run_id=:id"""


ANALYST_RATE = 200
ANALYST_SECONDS = 3600


class Config(BaseModel):
    scenario: Literal["mixed", "showcase"]
    count: int = Field(ge=1, le=ANALYST_RATE * ANALYST_SECONDS)
    rate: int = Field(ge=1, le=ANALYST_RATE)


def finish(c, run, status):
    query(c,"UPDATE simulations SET status=:status,ended=now() WHERE id=:id AND status='Running'",
          status=status,id=run)


def recover():
    # Compose runs a single API process. Its previous background jobs ended with it.
    with engine.begin() as c:
        query(c,"UPDATE simulations SET status='Failed',ended=now() WHERE status='Running'")


def records(config, seed):
    if config.scenario == "showcase":
        return showcase_records(seed, config.count)
    return generate(seed, config.count, "demo")


def submit_group(client, payments, deadline):
    # Keep receipts for the same account in generator order.
    for clock, payment, scheduled in payments:
        time.sleep(max(0, scheduled - time.monotonic()))
        while time.monotonic() < deadline:
            result = client.post("/v1/transactions", json=payment.model_dump(mode="json"),
                                 headers={"X-Simulated-At": clock.isoformat()})
            if result.status_code != 429:
                result.raise_for_status()
                break
            time.sleep(1)
        else:
            raise TimeoutError("Simulation generation deadline reached")


def produce(run, seed, config, elevated):
    deadline = time.monotonic() + (ANALYST_SECONDS + 240 if elevated else 240)
    try:
        stream = iter(records(config, seed))
        batch_size = min(16, max(1, config.rate // 10)) if elevated else 1
        with httpx.Client(base_url="http://api:8000", timeout=8,
                          headers={"Authorization":"Bearer "+os.environ["SIMULATOR_TOKEN"]}) as client, ThreadPoolExecutor(max_workers=batch_size) as pool:
            scheduled = time.monotonic()
            while batch := list(islice(stream, batch_size)):
                groups = {}
                # Avoid catch-up bursts after a slow batch.
                scheduled = max(scheduled, time.monotonic())
                for clock, payment, _ in batch:
                    groups.setdefault(payment.account_id, []).append((clock, payment, scheduled))
                    scheduled += 1 / config.rate
                with engine.begin() as c:
                    status = query(c,"SELECT status FROM simulations WHERE id=:id FOR UPDATE",id=run).scalar_one()
                    if status != "Running":
                        return
                    jobs = [pool.submit(submit_group,client,group,deadline) for group in groups.values()]
                    for job in jobs:
                        job.result()
        while time.monotonic() < deadline:
            with engine.begin() as c:
                if query(c,"SELECT status FROM simulations WHERE id=:id",id=run).scalar_one() != "Running":
                    return
                if query(c,COUNTS,id=run).mappings().one()["processed"] == config.count:
                    finish(c,run,"Completed")
                    return
            time.sleep(.5)
    finally:
        with engine.begin() as c:
            finish(c,run,"Failed")


@router.post("",status_code=202)
def start(config:Config, request:Request,response:Response,tasks:BackgroundTasks):
    origin(request)
    session = session_user(request)
    analyst = bool(session and session["role"] == "analyst")
    elevated = config.count > 200 or config.rate > 10
    if elevated and not analyst:
        raise HTTPException(422,"Guest and viewer limits are 200 payments and 10 payments per second")
    if analyst and config.count > config.rate * ANALYST_SECONDS:
        raise HTTPException(422,"Choose a count that fits within one hour at the selected rate")
    if config.scenario=="showcase" and config.count<7:
        raise HTTPException(422,"Showcase requires at least seven payments")
    token=request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
    owner=digest(token)
    peer=digest(request.headers.get("x-real-ip",request.client.host))
    with engine.begin() as c:
        query(c,"SELECT pg_advisory_xact_lock(715306)")
        query(c,"""UPDATE simulations SET status='Failed',ended=now() WHERE status='Running'
                   AND started < now() - CASE WHEN target>200 OR rate>10 THEN interval '65 minutes'
                                              ELSE interval '5 minutes' END""")
        if query(c,"SELECT 1 FROM simulations WHERE status='Running'").scalar():
            raise HTTPException(409,"Another simulation is running")
        if not analyst and query(c,"""SELECT count(*) FROM simulations WHERE started>now()-interval '10 minutes'
                    AND (owner=:owner OR peer=:peer)""",owner=owner,peer=peer).scalar_one()>=6:
            raise HTTPException(429,"Start limit reached. Try again in ten minutes.")
        seed=secrets.randbits(52)
        run=next(iter(records(config,seed)))[1].run_id
        query(c,"INSERT INTO runs(id,seed,generator_version,clock_mode) VALUES(:id,:seed,:v,'logical')",
              id=run,seed=seed,v=VERSION if config.scenario=="mixed" else "showcase-v1")
        query(c,"INSERT INTO simulations(id,owner,peer,scenario,target,rate,status) VALUES(:id,:owner,:peer,:scenario,:target,:rate,'Running')",
              id=run,owner=owner,peer=peer,scenario=config.scenario,target=config.count,rate=config.rate)
    response.set_cookie(COOKIE,token,httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=2592000)
    tasks.add_task(produce,run,seed,config,elevated)
    return {"id":run}


def owned(c,run,request):
    row=query(c,"SELECT id,scenario,target,rate,status,started,ended FROM simulations WHERE id=:id AND owner=:owner",
              id=run,owner=digest(request.cookies.get(COOKIE,""))).mappings().one_or_none()
    if not row:
        raise HTTPException(404,"Unknown simulation")
    return row


@router.post("/{run}/stop")
def stop(run:UUID,request:Request):
    origin(request)
    with engine.begin() as c:
        owned(c,run,request)
        finish(c,run,"Stopped")
    return {"id":run}


@router.get("")
def history(request:Request):
    with engine.connect() as c:
        return query(c,"SELECT id,scenario,status FROM simulations WHERE owner=:owner ORDER BY started DESC LIMIT 100",
                     owner=digest(request.cookies.get(COOKIE,""))).mappings().all()


@router.get("/{run}")
def monitor(run:UUID,request:Request):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
        row=owned(c,run,request)
        counts=query(c,COUNTS,id=run).mappings().one()
        items=query(c,f"""SELECT t.id,t.received_at,t.amount_minor,d.action,d.score,{STATE} AS state FROM transactions t
            LEFT JOIN decisions d ON d.transaction_id=t.id WHERE t.run_id=:id ORDER BY t.received_at DESC LIMIT 200""",id=run).mappings().all()
        return dict(**row,**counts,items=items)


@router.get("/{run}/transactions/{tx}")
def evidence(run:UUID,tx:UUID,request:Request):
    from apps.api.main import payment_evidence
    with engine.connect() as c:
        owned(c,run,request)
        if not query(c,"SELECT 1 FROM transactions WHERE run_id=:id AND id=:tx",id=run,tx=tx).scalar():
            raise HTTPException(404,"Unknown payment")
    return payment_evidence(tx)
