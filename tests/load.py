import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx
from confluent_kafka import Consumer, TopicPartition

from packages.contracts.stream import INPUT, query
from packages.db.connect import engine
from simulator.generate import VERSION, generate
from simulator.live import URL
from tests.pipeline import GROUP, drain


async def traffic(rate, seconds):
    seed = uuid4().int % (2**31)
    records = list(generate(seed, rate*seconds, "demo"))
    run = records[0][1].run_id
    with engine.begin() as c:
        query(c,"INSERT INTO runs(id,seed,generator_version,clock_mode) VALUES(:r,:s,:v,'logical')",
              r=run,s=seed,v=VERSION)
    statuses = Counter()
    async with httpx.AsyncClient(base_url=URL, headers={"Authorization":"Bearer "+os.environ["SIMULATOR_TOKEN"]}) as client:
        async def post(clock,payment):
            try:
                res=await client.post("/v1/transactions",json=payment.model_dump(mode="json"),
                                      headers={"X-Simulated-At":clock.isoformat()})
                statuses[str(res.status_code)] += 1
            except httpx.HTTPError:
                statuses["transport_error"] += 1
        start=time.monotonic()
        tasks=[]
        for i,(clock,p,_) in enumerate(records):
            await asyncio.sleep(max(0,start+i/rate-time.monotonic()))
            tasks.append(asyncio.create_task(post(clock,p)))
        await asyncio.gather(*tasks)
        await asyncio.sleep(max(0,start+seconds-time.monotonic()))
    return str(run),dict(statuses),time.monotonic()-start


def load(rate, seconds):
    if rate<=0 or seconds<=0:
        raise ValueError("rate and duration must be positive")
    warm, statuses, _ = asyncio.run(traffic(rate,30))
    if statuses != {"202":30*rate}:
        raise RuntimeError(f"Warm-up failed: {statuses}")
    drain(warm)
    run,statuses,duration=asyncio.run(traffic(rate,seconds))
    stop=time.monotonic()
    counts=drain(run)
    probe=Consumer({"bootstrap.servers":"kafka:9092","group.id":GROUP,"enable.auto.commit":False})
    try:
        parts=[TopicPartition(INPUT,p) for p in probe.list_topics(INPUT,timeout=10).topics[INPUT].partitions]
        while True:
            lag=sum(max(0,probe.get_watermark_offsets(tp,timeout=5)[1]-max(0,tp.offset))
                    for tp in probe.committed(parts,timeout=5))
            if lag==0 or time.monotonic()-stop>60:
                break
            time.sleep(.1)
    finally:
        probe.close()
    with engine.connect() as c:
        latency=query(c,"""SELECT percentile_cont(ARRAY[0.5,0.95,0.99]) WITHIN GROUP(ORDER BY extract(epoch FROM pg_xact_commit_timestamp(d.xmin)-t.received_at)*1000)
            FROM decisions d JOIN transactions t ON t.id=d.transaction_id WHERE t.run_id=:r""",r=run).scalar_one()
    result=dict(run=run,warmup_run=warm,rate=rate,seconds=seconds,duration=duration,http=statuses,
                accepted_per_second=counts["accepted"]/duration,latency_ms=latency,counts=counts,
                final_lag=lag,drain_seconds=time.monotonic()-stop)
    result["passed"]=(statuses=={"202":rate*seconds} and counts["decided"]==rate*seconds and
                      latency[1]<1000 and lag==0 and result["drain_seconds"]<=30)
    Path("/evidence/load.json").write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    if not result["passed"]:
        raise RuntimeError("Measured load missed release target")
