from prometheus_client import Counter, Gauge, Histogram, start_http_server

accepted = Counter("accepted","New durable receipts")
decisions = Counter("decisions","New committed decisions",["action"])
duplicates = Counter("duplicate","Deduplicated receipts or deliveries")
retries = Counter("retry","Retried work",["stage"])
dlq = Counter("dlq","New durable quarantine records")
fallbacks = Counter("redis_fallback","History reads rebuilt from SQL")
latency = Histogram("receipt_to_decision_seconds","Wall time from receipt to committed decision",
                    buckets=(.01,.025,.05,.1,.25,.5,1,2,5,10,30,60,120))
scoring = Histogram("scoring_seconds","Feature and scoring transaction duration")
lag = Gauge("consumer_lag","Uncommitted records",["partition"])
ready = Gauge("model_ready","Verified scoring model loaded")
pending = Gauge("outbox_pending","Unpublished outbox records")
age = Gauge("outbox_oldest_seconds","Age of oldest unpublished event")


def serve():
    start_http_server(9100)
