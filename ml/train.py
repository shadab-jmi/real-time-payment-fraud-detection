import json
import shutil
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import xgboost as xgb
from sqlalchemy import text

from ml.artifact import MANIFEST, ROOT, STORE, load, sha
from ml.policy import bootstrap, evaluate, select_policy
from ml.verify import check_fixtures
from packages.db.connect import engine
from packages.features.history import ReplayHistory
from simulator.generate import VERSION, generate


def source_revision():
    paths = sorted(p for part in ("ml","packages","simulator") for p in (ROOT/part).rglob("*")
                   if p.suffix in {".py",".json"})
    paths += [ROOT/"requirements.lock", ROOT/"ops/tools.Dockerfile"] if (ROOT/"ops/tools.Dockerfile").exists() else [ROOT/"requirements.lock"]
    return sha(json.dumps([(str(p.relative_to(ROOT)),sha(p.read_bytes())) for p in paths]).encode())


def fit(folder, seed, rows, revision):
    history = ReplayHistory()
    vectors, labels, scenarios, timestamps = [], [], [], []
    for clock, payment, truth in generate(seed, rows):
        _, vector = history.accept(payment,clock)
        vectors.append(vector)
        labels.append(truth["label"])
        scenarios.append(truth["scenario"])
        timestamps.append(clock)
    X, y, scenario = np.asarray(vectors), np.asarray(labels), np.asarray(scenarios)
    names = [f["name"] for f in json.loads(MANIFEST.read_bytes())]
    splits = dict(train=slice(0,rows*70//100), validation=slice(rows*70//100,rows*85//100),
                  test=slice(rows*85//100,rows))
    t, v, h = splits.values()
    def matrix(idx):
        return xgb.DMatrix(X[idx],label=y[idx],feature_names=names,nthread=2)
    dt, dv = matrix(t), matrix(v)
    attempts = []
    for depth in (4, 3, 5, 2, 6):
        params = dict(objective="binary:logistic",tree_method="hist", device="cpu",max_depth=depth,
                      eta=0.08,seed=seed,nthread=2,eval_metric="aucpr")
        trained = xgb.train(params,dt,400,[(dv,"validation")],early_stopping_rounds=35,verbose_eval=False)
        model = trained[:trained.best_iteration+1]
        validation_scores = model.predict(dv)
        policy = select_policy(y[v],validation_scores)
        attempts.append(dict(depth=depth,trees=model.num_boosted_rounds(),feasible=policy is not None))
        if policy:
            break
    else:
        raise RuntimeError(f"No feasible validation policy. Development results: {attempts}")
    model.save_model(folder/"booster.json")
    shutil.copyfile(MANIFEST,folder/"manifest.json")
    shutil.copyfile(ROOT/"requirements.lock",folder/"requirements.lock")
    hashes = {p.name:sha(p.read_bytes()) for p in folder.iterdir()}
    frozen = load(folder, hashes)
    parity = check_fixtures(frozen, policy)
    # Fixed benchmark: one point each for amount >= INR 1000 and any prior attempt in 10m.
    baseline = (X[:,0]>=np.log1p(1000)).astype(int) + (X[:,8]>=1)
    metrics = {"parity":parity}
    for name, idx in splits.items():
        scores = frozen.predict(matrix(idx))
        result = evaluate(y[idx],scores,baseline[idx],scenario[idx],policy)
        result["scenarios"] = dict(Counter(scenario[idx].tolist()))
        metrics[name] = result
        if name == "test":
            metrics["daily_bootstrap_95"] = bootstrap(y[idx],scores,
                np.array([d.date().toordinal() for d in timestamps[idx]]),policy,seed)
    lineage = dict(seed=seed,rows=rows,run_id=str(payment.run_id),generator=VERSION,source_sha256=revision,
                   trees=frozen.num_boosted_rounds(),params=params,development=attempts,
                   splits={n:[timestamps[s][0].isoformat(),timestamps[s][-1].isoformat()] for n,s in splits.items()})
    report = dict(lineage=lineage,metrics=metrics,review=policy[0],block=policy[1],hashes=hashes.copy())
    (folder/"release.json").write_text(json.dumps(report,indent=2,sort_keys=True))
    return report


def train(seed, rows):
    if rows < 1000:
        raise ValueError("Insufficient rows for temporal policy selection")
    revision = source_revision()
    version = f"xgb-v1-{seed}-{rows}-{revision[:16]}"
    dest = STORE/version
    with engine.begin() as conn:
        conn.exec_driver_sql("SELECT pg_advisory_xact_lock(715303)")
        if not dest.exists():
            with TemporaryDirectory(dir=STORE) as temp:
                stage = Path(temp)/"release"
                stage.mkdir()
                fit(stage,seed,rows,revision)
                stage.rename(dest)
        release = json.loads((dest/"release.json").read_bytes())
        hashes = {**release["hashes"],"release.json":sha((dest/"release.json").read_bytes())}
        load(dest,hashes)
        m = release["metrics"]
        test = m["test"]
        if test["positives"] < 100 or test["ap"] <= max(test["prevalence"],test["baseline_ap"]):
            raise RuntimeError(f"Frozen release failed A2. Results retained at {dest}: {test}")
        params = dict(id=uuid5(NAMESPACE_URL,version),version=version,schema="features-v1",
                      manifest=MANIFEST.read_text(),digest=hashes["manifest.json"],path=str(dest/"booster.json"),
                      checksum=hashes["booster.json"],metrics=json.dumps(m),review=release["review"],block=release["block"],
                      lineage=json.dumps({**release["lineage"],"hashes":hashes}))
        conn.execute(text("""INSERT INTO feature_schemas(id,ordered_manifest,digest)
            VALUES (:schema,CAST(:manifest AS jsonb),:digest) ON CONFLICT (id) DO NOTHING"""),params)
        registered = conn.execute(text("SELECT digest FROM feature_schemas WHERE id=:schema"),params).scalar_one()
        if registered != params["digest"]:
            raise ValueError("Registry feature manifest differs from expected schema")
        conn.execute(text("""INSERT INTO model_versions(id,version,schema_id,artifact_path,artifact_sha256,metrics,lineage,
            review_threshold,block_threshold) VALUES (:id,:version,:schema,:path,:checksum,CAST(:metrics AS jsonb),
            CAST(:lineage AS jsonb),:review,:block) ON CONFLICT (version) DO NOTHING"""),params)
        existing = conn.execute(text("SELECT lineage FROM model_versions WHERE version=:version"),params).scalar_one()
        if existing["hashes"] != hashes:
            raise ValueError("Existing immutable release differs from stored checksums")
        conn.execute(text("UPDATE model_versions SET active=false WHERE active AND id<>:id"),params)
        conn.execute(text("UPDATE model_versions SET active=true WHERE id=:id"),params)
    print(json.dumps(dict(version=version,review=params["review"],block=params["block"],metrics=m),sort_keys=True))
