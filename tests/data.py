import json
from math import isclose, log1p
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from packages.contracts.http import registered_payment
from packages.features.history import ReplayHistory, StreamingHistory
from simulator.dataset import ROOT, dataset, digest, fixture
from simulator.generate import generate


def check_data():
    records = list(fixture(42))
    assert digest(records) == "97a59e3d01b75a1c9400dccb9b8bbccc1e85a85cde08cfb0daf90b26cf481c22"
    assert digest(records) == digest(fixture(42))
    assert digest(generate(42,1000)) == digest(generate(42,1000))
    assert digest(generate(42,10)) != digest(generate(42,10,"demo"))
    print("PASS deterministic canonical records and labels; independent demo namespace")
    offline, online = ReplayHistory(), StreamingHistory()
    expected = [(0,0,0,1),(1,100,1,1),(2,400,2,0),(3,402,2,0),(0,0,2,1),(0,0,1,1)]
    manifest = json.loads((ROOT/"packages/features/manifest-v1.json").read_text())
    for i,(clock,p,_) in enumerate(records):
        row, v = offline.accept(p,clock)
        assert (row,v) == online.accept(p,clock)
        assert row.seq == i+1 and len(v) == len(manifest)
        assert v[8:] == expected[i], (i,v[8:])
        assert v[7] == int(p.device_id is None)
        if i in (0,1):
            amount = [100,300][i]
            assert all(isclose(a,b,abs_tol=1e-12) for a,b in zip(v,(log1p(amount),0,1,0,0,0,1,0,*expected[i])))
        if i == 3:
            assert row.accepted_at == records[2][0]
    print("PASS both adapters; full two-payment calculation; inclusive boundaries, ties, late event, missing and new devices")
    app = FastAPI()
    first = records[0][1]
    @app.post("/v1/transactions")
    def validate(payment=Depends(registered_payment({first.run_id}))):
        return {"transaction_id":payment.transaction_id}
    with TestClient(app) as client:
        body = first.model_dump(mode="json")
        assert client.post("/v1/transactions",json=body).status_code == 200
        for change in ({"label":1},{"run_id":str(first.transaction_id)},{"currency":"USD"},{"channel":"atm"},
                       {"amount_minor":True},{"amount_minor":100000001},{"merchant_country":"ZZ"},
                       {"event_time":"2026-01-01T12:00:00"},{"device_id":"real name"}):
            assert client.post("/v1/transactions",json={**body,**change}).status_code == 422, change
    print("PASS FastAPI contract, including 422 for an extra label and for an unregistered run")
    report = dataset(42,100000,"training")
    assert 1000 <= sum(v["positives"] for v in report.values()) <= 3000
    print(json.dumps(report,sort_keys=True))
