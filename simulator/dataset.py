import hashlib
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from packages.contracts.payment import PaymentRequestV1
from simulator.generate import START, VERSION, generate

ROOT = Path(__file__).resolve().parents[1]


def fixture(seed):
    run = uuid5(NAMESPACE_URL, f"fixture-v1/{seed}")
    specs = json.loads((ROOT / "packages/contracts/fixture-v1.json").read_text())
    for ordinal, spec in enumerate(specs):
        p = PaymentRequestV1(transaction_id=uuid5(run,str(ordinal)), run_id=run,
            account_id=f"acct_{run.hex}", merchant_id=f"mer_{run.hex}", device_id=(
                f"dev_{run.hex}_{spec['device']}" if spec["device"] else None),
            event_time=START+timedelta(seconds=spec["event_seconds"]), amount_minor=spec["amount_minor"],
            channel="web", currency="INR", account_country="IN", merchant_country="IN")
        yield START+timedelta(seconds=spec["receipt_seconds"]), p, {
            "transaction_id":str(p.transaction_id),"label":spec["label"],"scenario":spec["scenario"],
            "generator_version":"fixture-v1","seed":seed}


def digest(records):
    h = hashlib.sha256()
    for receipt, p, label in records:
        h.update(json.dumps([receipt.isoformat(),p.canonical(),label],sort_keys=True).encode()+b"\n")
    return h.hexdigest()


def dataset(seed, rows, purpose, output=None):
    from contextlib import ExitStack
    counts = {s:Counter() for s in ("train","validation","test")}
    scenarios = {s:Counter() for s in counts}
    with ExitStack() as stack:
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=False)
            payments = stack.enter_context((folder/"payments.jsonl").open("w",encoding="utf-8"))
            labels = stack.enter_context((folder/"evaluation-labels.jsonl").open("w",encoding="utf-8"))
        for i, (received, p, truth) in enumerate(generate(seed,rows,purpose)):
            split = "train" if i < rows*0.70 else "validation" if i < rows*0.85 else "test"
            counts[split].update(rows=1, positives=truth["label"])
            scenarios[split][truth["scenario"]] += 1
            if output:
                payments.write(json.dumps({"received_at":received.isoformat(),"payment":json.loads(p.canonical())})+"\n")
                labels.write(json.dumps(truth)+"\n")
        report = {s:dict(**v,prevalence=v["positives"]/v["rows"] if v["rows"] else None,
                         scenarios=dict(scenarios[s])) for s,v in counts.items()}
        if output:
            (folder/"manifest.json").write_text(json.dumps(dict(seed=seed,rows=rows,purpose=purpose,
                generator_version=VERSION,run_id=str(p.run_id),splits=report),indent=2),encoding="utf-8")
        return report
