import argparse
import json
import os

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from packages.db.connect import engine, upgrade

TOPICS = ("payments.received.v1", "fraud.decided.v1", "fraud.deadletter.v1")


def provision_topics():
    admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]})
    futures = admin.create_topics([NewTopic(name, 3, 1) for name in TOPICS], request_timeout=30)
    for name, result in futures.items():
        try:
            result.result()
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
        topic = admin.list_topics(topic=name, timeout=15).topics[name]
        if topic.error or set(topic.partitions) != {0, 1, 2}:
            raise RuntimeError(f"Unexpected metadata for {name}: {topic.error}, {list(topic.partitions)}")
        if any(len(p.replicas) != 1 or p.leader < 0 for p in topic.partitions.values()):
            raise RuntimeError(f"Missing leader or unexpected replication factor for {name}")
        print(f"{name}: 3 partitions, replication factor 1")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("topics")
    repair=sub.add_parser("repair-quarantine")
    repair.add_argument("id")
    sub.add_parser("seed-users")
    perf=sub.add_parser("load")
    perf.add_argument("--rate",type=int,default=20)
    perf.add_argument("--seconds",type=int,default=120)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--run-id")
    sim = sub.add_parser("simulate")
    sim.add_argument("--scenario",choices=["mixed","showcase"],default="mixed")
    sim.add_argument("--count",type=int,default=200)
    sim.add_argument("--seed",type=int,default=17)
    sim.add_argument("--rate",type=float,default=0)
    train_cmd = sub.add_parser("train")
    train_cmd.add_argument("--seed",type=int,default=42)
    train_cmd.add_argument("--rows",type=int,default=100000)
    fixture_cmd = sub.add_parser("fixture")
    fixture_cmd.add_argument("--seed", type=int, default=42)
    gen = sub.add_parser("generate")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--rows", type=int, default=100000)
    gen.add_argument("--purpose", choices=["training","demo"], default="training")
    gen.add_argument("--output", required=True)
    check = sub.add_parser("check")
    check.add_argument("suite", choices=["storage", "upgrade","data","model","pipeline","ui","recovery"])
    args = parser.parse_args()
    if args.command == "migrate":
        with engine.begin() as conn:
            revision = upgrade(conn)
        print(f"Migration head: {revision}")
    elif args.command == "load":
        from tests.load import load
        load(args.rate,args.seconds)
    elif args.command == "repair-quarantine":
        from tests.release import repair
        from uuid import UUID
        repair(UUID(args.id))
        print("Repair recorded; canonical receipt scheduled for replay")
    elif args.command == "check" and args.suite == "recovery":
        from tests.release import check_recovery
        check_recovery()
    elif args.command == "topics":
        provision_topics()
    elif args.command == "seed-users":
        from simulator.live import seed_users
        seed_users()
    elif args.command == "simulate":
        from simulator.live import showcase, simulate
        generator = showcase if args.scenario == "showcase" else simulate
        generator(args.seed,args.count,args.rate)
    elif args.command == "reconcile":
        from tests.pipeline import drain
        print(drain(args.run_id))
    elif args.command == "check" and args.suite == "pipeline":
        from tests.pipeline import check_pipeline
        check_pipeline()
    elif args.command == "check" and args.suite == "ui":
        from tests.ui import check_ui
        check_ui()
    elif args.command == "train":
        from ml.train import train
        train(args.seed,args.rows)
    elif args.command == "check" and args.suite == "model":
        from ml.verify import check_model
        check_model()
    elif args.command == "fixture":
        from simulator.dataset import digest, fixture
        rows = list(fixture(args.seed))
        print(f"{len(rows)} fixture receipts, SHA-256 {digest(rows)}")
    elif args.command == "generate":
        from simulator.dataset import dataset
        print(json.dumps(dataset(args.seed,args.rows,args.purpose,args.output),sort_keys=True))
    elif args.suite == "data":
        from tests.data import check_data
        check_data()
    else:
        from tests.storage import check_storage, check_upgrade
        with engine.connect() as conn:
            if args.suite == "upgrade":
                check_upgrade(conn)
            else:
                check_storage(conn)
            conn.rollback()
        print("PASS: fixture transaction rolled back")


if __name__ == "__main__":
    main()
