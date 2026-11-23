"""Exercise the web proxy, cookies, role checks and case audit transaction."""
import hashlib
import json
import os
from uuid import uuid4

import httpx

from packages.contracts.stream import query
from packages.db.connect import engine
from simulator.dataset import fixture
from simulator.live import CREDENTIALS,submit
from tests.pipeline import drain


def fresh_case():
    seed=uuid4().int % (2**31)
    run=submit(fixture(seed),seed,"fixture-v1")
    drain(run)
    with engine.connect() as c:
        return str(query(c,"SELECT case_id FROM v_case_evidence_v1 WHERE run_id=:r ORDER BY case_id LIMIT 1",r=run).scalar_one())


def check_ui():
    passwords=json.loads(CREDENTIALS.read_bytes())
    origin="http://localhost:"+os.getenv("WEB_PORT","3000")
    with httpx.Client(base_url="http://web:8080",headers={"Origin":origin}) as browser:
        assert browser.get("/v1/cases").status_code==401
        def login(name):
            response=browser.post("/v1/session",json=dict(username=name,password=passwords[name]))
            response.raise_for_status()
            assert "httponly" in response.headers["set-cookie"].lower()
            browser.headers["X-CSRF-Token"]=response.json()["csrf"]
        cid=fresh_case()
        url="/v1/cases/"+cid
        login("viewer")
        assert browser.get(url).status_code==200
        overview=browser.get("/v1/summary")
        overview.raise_for_status()
        assert sum(p["total"] for p in overview.json()["series"])==overview.json()["accepted"]
        note={"id":str(uuid4()),"body":"Phase 5 synthetic audit check"}
        assert browser.post(url+"/notes",json=note).status_code==403
        assert browser.delete("/v1/session").status_code==200
        assert browser.get("/v1/me").status_code==401
        login("analyst")
        for headers in ({"X-CSRF-Token":"invalid"},{"Origin":"http://invalid.local"}):
            assert browser.post(url+"/notes",headers=headers,json=note).status_code==403
        original=browser.get(url).json()["version"]
        for _ in range(2):
            browser.post(url+"/notes",json=note).raise_for_status()
        assert browser.post(url+"/notes",json={**note,"body":"different"}).status_code==409
        assert browser.patch(url,json=dict(status="investigating",expected_version=original)).status_code==409
        def transition(status,**extra):
            version=browser.get(url).json()["version"]
            browser.patch(url,json=dict(status=status,expected_version=version,**extra)).raise_for_status()
        transition("investigating")
        transition("resolved",resolution="Verified synthetic evidence")
        case=browser.get(url).json()
        assert case["owner"]=="analyst" and case["status"]=="resolved" and len(case["notes"])==1
        assert [e["kind"] for e in case["timeline"]]==["opened","note_added","investigating","resolved"]
        first=browser.get("/v1/transactions?limit=2").json()
        second=browser.get("/v1/transactions",params={"limit":2,"cursor":first["next_cursor"]}).json()
        assert len(first["items"])==len(second["items"])==2
        assert {r["id"] for r in first["items"]}.isdisjoint(r["id"] for r in second["items"])
        assert browser.get("/v1/transactions?cursor=invalid").status_code==422
        assert browser.get("/v1/cases?run_id="+str(uuid4())).json()["items"]==[]
        token_hash=hashlib.sha256(browser.cookies["session"].encode()).hexdigest()
        with engine.begin() as c:
            query(c,"UPDATE sessions SET expires_at=now()-interval '1 second' WHERE token_hash=:h",h=token_hash)
        assert browser.get("/v1/me").status_code==401
        print("PASS expired session")
        print("PASS proxy sessions, permissions, CSRF, origin, pagination, empty results, idempotent notes, stale edit and resolution",cid)
