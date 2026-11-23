from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, log1p, pi, sin

from packages.contracts.payment import PaymentRequestV1

TEN_MINUTES = timedelta(minutes=10)
DAY = timedelta(days=1)


@dataclass(frozen=True)
class Accepted:
    payment: PaymentRequestV1
    accepted_at: datetime
    seq: int


def vector(current, prior):
    p = current.payment
    recent = [x for x in prior if x.accepted_at >= current.accepted_at - TEN_MINUTES]
    devices = {x.payment.device_id for x in prior if x.payment.device_id is not None}
    angle = 2 * pi * p.event_time.hour / 24
    return (log1p(p.amount_minor / 100), int(p.account_country != p.merchant_country),
            int(p.channel == "web"), int(p.channel == "app"), int(p.channel == "pos"),
            sin(angle), cos(angle), int(p.device_id is None), len(recent),
            sum(x.payment.amount_minor for x in recent) / 100, len(devices),
            int(p.device_id is not None and p.device_id not in devices))


class ReplayHistory:
    """Reference adapter scanning retained accepted rows for an account."""
    def __init__(self):
        self.rows = defaultdict(list)

    def prior(self, account, cutoff):
        return [r for r in self.rows[account] if r.accepted_at >= cutoff]

    def accept(self, payment, received_at):
        if received_at.utcoffset() is None:
            raise ValueError("Receipt clock requires a timezone")
        rows = self.rows[payment.account_id]
        previous = rows[-1] if rows else None
        current = Accepted(payment, max(received_at, previous.accepted_at) if previous else received_at,
                           previous.seq + 1 if previous else 1)
        features = vector(current, self.prior(payment.account_id, current.accepted_at - DAY))
        rows.append(current)
        return current, features


class StreamingHistory(ReplayHistory):
    """Incremental adapter retaining only the inclusive 24-hour window."""
    def __init__(self):
        self.rows = defaultdict(deque)

    def prior(self, account, cutoff):
        rows = self.rows[account]
        while rows and rows[0].accepted_at < cutoff:
            rows.popleft()
        return rows
