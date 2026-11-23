import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from ml.artifact import active, load, score, sha
from ml.policy import actions
from packages.features.history import ReplayHistory, StreamingHistory
from simulator.dataset import fixture
from simulator.generate import generate


def check_fixtures(model, policy):
    offline, online = ReplayHistory(), StreamingHistory()
    records = list(fixture(42)) + list(generate(17,94,"demo"))
    left, right = [], []
    for clock,p,_ in records:
        left.append(offline.accept(p,clock)[1])
        right.append(online.accept(p,clock)[1])
    for i,name in enumerate(model.feature_names):
        np.testing.assert_allclose(np.array(left)[:,i],np.array(right)[:,i],rtol=0,atol=1e-6,err_msg=name)
    a, decisions, _, err = score(model,left,policy)
    b, compared, contrib, _ = score(model,right,policy)
    np.testing.assert_allclose(a,b,atol=1e-6,rtol=0)
    np.testing.assert_array_equal(decisions,compared)
    r, threshold = policy
    np.testing.assert_array_equal(actions(np.array([np.nextafter(r,0),r,np.nextafter(threshold,0),threshold]),*policy),
                                  [0,1,1,2])
    i = int(np.argmax(a))
    factors = dict(zip(model.feature_names,map(float,contrib[i,:-1])))
    return dict(rows=len(records),max_margin_error=float(max(err)),example=dict(score=float(a[i]),
        transaction=str(records[i][1].transaction_id),bias=float(contrib[i,-1]),
        strongest_positive=max(factors.items(),key=lambda x:x[1]),strongest_negative=min(factors.items(),key=lambda x:x[1])))


def check_model():
    row, model = active()
    parity = check_fixtures(model,(row["review_threshold"],row["block_threshold"]))
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = Path(row["artifact_path"]).parent
        for p in source.iterdir():
            shutil.copyfile(p,folder/p.name)
        hashes = row["lineage"]["hashes"].copy()
        (folder/"manifest.json").write_text("[]")
        for incompatible in (False,True):
            if incompatible:
                hashes["manifest.json"] = sha(b"[]")
            try:
                load(folder,hashes)
            except ValueError:
                continue
            raise RuntimeError("Invalid release accepted")
    print(json.dumps(dict(version=row["version"],parity=parity,metrics=row["metrics"]),sort_keys=True))
    print("PASS model readiness, corrupt/incompatible artifact rejection, parity and threshold boundaries")
