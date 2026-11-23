import hashlib
import json
from pathlib import Path

import numpy as np
import xgboost as xgb
from sqlalchemy import text

from packages.db.connect import engine
from ml.policy import actions

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packages/features/manifest-v1.json"
STORE = Path("/artifacts")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(folder, expected):
    for name, digest in expected.items():
        if sha((folder/name).read_bytes()) != digest:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    if (folder/"manifest.json").read_bytes() != MANIFEST.read_bytes():
        raise ValueError("Unexpected feature manifest")
    model = xgb.Booster(params={"nthread":2})
    model.load_model(folder/"booster.json")
    names = [f["name"] for f in json.loads(MANIFEST.read_bytes())]
    if model.feature_names != names:
        raise ValueError("Booster feature order mismatch")
    return model


def active():
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM model_versions WHERE active")).mappings().one()
    folder = Path(row["artifact_path"]).parent
    hashes = dict(row["lineage"]["hashes"])
    hashes["booster.json"] = row["artifact_sha256"]
    return row, load(folder, hashes)


def predict_evidence(model, vectors, thresholds):
    matrix = xgb.DMatrix(np.asarray(vectors),feature_names=model.feature_names,nthread=2)
    scores = model.predict(matrix).astype(float)
    margin = model.predict(matrix,output_margin=True)
    contributions = model.predict(matrix,pred_contribs=True,approx_contribs=False)
    error = np.abs(contributions.sum(axis=1,dtype=float)-margin)
    if np.max(error) > 1e-4:
        raise ValueError("Contribution sum mismatch")
    return scores, actions(scores,*thresholds), contributions, error, margin


def score(model, vectors, thresholds):
    return predict_evidence(model, vectors, thresholds)[:4]
