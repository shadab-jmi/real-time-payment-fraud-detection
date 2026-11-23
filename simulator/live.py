import json
import os
import secrets
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from argon2 import PasswordHasher

from packages.contracts.stream import query
from packages.db.connect import engine
from simulator.generate import VERSION, generate

URL = "http://api:8000"
CREDENTIALS = Path("/credentials/local-users.json")


def seed_users():
    with engine.begin() as conn:
        query(conn,"SELECT pg_advisory_xact_lock(715305)")
        if not CREDENTIALS.exists():
            passwords = {role:secrets.token_urlsafe(24) for role in ("viewer","analyst")}
            temp = CREDENTIALS.with_suffix(".tmp")
            with open(temp,"w",opener=lambda p,f:os.open(p,f,0o600)) as output:
                json.dump(passwords,output)
            temp.replace(CREDENTIALS)
        for role,password in json.loads(CREDENTIALS.read_bytes()).items():
            query(conn,"""INSERT INTO users(id,username,password_hash,role) VALUES(:id,:role,:hash,:role)
                ON CONFLICT(username) DO NOTHING""",id=uuid4(),role=role,hash=PasswordHasher().hash(password))
    print(f"Local login credentials: {CREDENTIALS} in the tools credential volume")


def submit(records,seed,version,rate=0):
    headers = {"Authorization":"Bearer "+os.environ["SIMULATOR_TOKEN"]}
    with httpx.Client(base_url=URL,headers=headers,timeout=15) as client:
        count = 0
        for clock,p,_ in records:
            if not count:
                with engine.begin() as conn:
                    query(conn,"""INSERT INTO runs(id,seed,generator_version,clock_mode)
                        VALUES(:id,:seed,:v,'logical') ON CONFLICT DO NOTHING""",id=p.run_id,seed=seed,v=version)
            response = client.post("/v1/transactions",json=p.model_dump(mode="json"),
                                   headers={"X-Simulated-At":clock.isoformat()})
            response.raise_for_status()
            if response.status_code!=202 or response.json()["transaction_id"]!=str(p.transaction_id):
                raise RuntimeError("Incorrect receipt")
            count+=1
            if rate:
                time.sleep(1/rate)
    print(f"run={p.run_id} accepted={count}")
    return p.run_id


def simulate(seed,count,rate):
    if count<=0 or rate<0:
        raise ValueError("Expected a positive count and nonnegative rate")
    return submit(generate(seed,count,"demo"),seed,VERSION,rate)


def showcase_records(seed, count):
    from simulator.dataset import fixture
    if count < 7:
        raise ValueError("Showcase requires at least seven attempts and nonnegative pacing")
    run=uuid5(NAMESPACE_URL,f"showcase-v1/{seed}/{count}")
    examples=list(fixture(seed))
    def records():
        for i,(clock,p,truth) in enumerate(generate(seed,count,"demo")):
            if i < len(examples):
                clock,p,truth=examples[i]
            original=p.run_id.hex
            fields={k:getattr(p,k).replace(original,run.hex) for k in ("account_id","merchant_id","device_id") if getattr(p,k)}
            if i==len(examples):
                fields.update(account_id="acct_"+run.hex+"_showcase",device_id="dev_"+run.hex+"_new",
                    merchant_country="GB",account_country="IN",amount_minor=100000,channel="web",
                    event_time=clock.replace(hour=0))
            yield clock,p.model_copy(update={**fields,"transaction_id":uuid5(run,str(i)),"run_id":run}),truth
    return records()


def showcase(seed, count, rate):
    if rate < 0:
        raise ValueError("Expected nonnegative pacing")
    return submit(showcase_records(seed,count),seed,"showcase-v1",rate)
