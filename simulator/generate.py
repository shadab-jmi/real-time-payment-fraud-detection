import random
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from packages.contracts.payment import PaymentRequestV1

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
VERSION = "payment-sim-v1"


def generate(seed=42, rows=100_000, purpose="training"):
    if rows <= 0 or purpose not in {"training", "demo"}:
        raise ValueError("Expected positive rows and a training or demo run")
    rng = random.Random(seed)
    run = uuid5(NAMESPACE_URL, f"{VERSION}/{purpose}/{seed}/{rows}")
    ns = run.hex
    habits = [(rng.randrange(200), rng.randint(1000, 25000), rng.randrange(24)) for _ in range(2000)]
    burst_left = 0
    for i in range(rows):
        received = START + timedelta(seconds=i * 30 * 86400 / rows)
        if burst_left:
            account = burst_account
            scenario = "testing"
            burst_left -= 1
        else:
            # Prefer an account active near this UTC hour.
            candidates = rng.choices(range(2000), k=3)
            account = min(candidates, key=lambda a: abs((habits[a][2]-received.hour+12) % 24-12))
            scenario = rng.choices(["normal","travel","large_bill","takeover","reuse","testing"],
                                   weights=[94,2,2,0.9,0.9,0.2])[0]
            if scenario == "testing":
                burst_account, burst_left = account, 3
        affinity, typical, _ = habits[account]
        merchant = affinity if rng.random() < 0.75 else rng.randrange(200)
        amount = max(1, int(typical * rng.lognormvariate(0, 0.7)))
        if scenario in {"takeover","large_bill"}:
            amount *= 8
        if scenario == "testing":
            amount = rng.randint(1, 1500)
        device = f"dev_{ns}_{account}"
        if scenario in {"reuse","takeover"}:
            device = f"dev_{ns}_shared{rng.randrange(8)}"
        payment = PaymentRequestV1(transaction_id=uuid5(run,str(i)), run_id=run,
            account_id=f"acct_{ns}_{account}", merchant_id=f"mer_{ns}_{merchant}",
            device_id=device if rng.random() > 0.03 else None, event_time=received, amount_minor=amount,
            currency="INR", channel=rng.choice(("app","web","pos")), account_country="IN",
            merchant_country="GB" if scenario in {"travel","takeover"} else "IN")
        probability = {"normal":0.002,"travel":0.04,"large_bill":0.035,"takeover":0.82,
                       "reuse":0.63,"testing":0.57}[scenario]
        yield received, payment, {"transaction_id":str(payment.transaction_id), "label":int(rng.random()<probability),
                                  "scenario":scenario,"generator_version":VERSION,"seed":seed}
