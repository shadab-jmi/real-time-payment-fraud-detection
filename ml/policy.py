import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


def actions(scores, review, block):
    return np.where(scores >= block, 2, np.where(scores >= review, 1, 0))


def select_policy(y, scores):
    order = np.argsort(-scores, kind="stable")
    sorted_scores = scores[order]
    ends = np.flatnonzero(np.r_[np.diff(sorted_scores) != 0, True]) + 1
    positives = np.cumsum(y[order])[ends - 1]
    best = None
    for i, count in enumerate(ends):
        if count < 20 or positives[i] < 0.8 * count:
            continue
        last = ends.searchsorted(count + len(y) // 20, side="right") - 1
        # Earliest cutoff attaining maximum recall minimizes review-only workload.
        j = max(i, int(positives.searchsorted(positives[last])))
        b = float(sorted_scores[count - 1])
        r = float(sorted_scores[ends[j] - 1])
        if i == j:
            # Put r in the gap above the highest approved score.
            r = np.nextafter(b, 0.0)
        key = (int(positives[j]), int(positives[i] - count), int(count - ends[j]))
        if 0 < r < b < 1 and (best is None or key > best[0]):
            best = key, (float(r), b)
    return best[1] if best else None


def confusion(y, flagged):
    tp = int(y[flagged].sum())
    fp = int(flagged.sum()) - tp
    fn = int(y.sum()) - tp
    tn = len(y) - tp - fp - fn
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, precision=tp/max(1,tp+fp), recall=tp/max(1,tp+fn))


def evaluate(y, scores, baseline, scenario, thresholds):
    a = actions(scores.astype(float), *thresholds)
    counts = dict(zip(("approve","review","block"), map(int,np.bincount(a,minlength=3))))
    return dict(rows=len(y), positives=int(y.sum()), prevalence=float(y.mean()),
                ap=float(average_precision_score(y,scores)), roc_auc=float(roc_auc_score(y,scores)),
                baseline_ap=float(average_precision_score(y,baseline)), counts=counts,
                rates={k:v/len(y) for k,v in counts.items()}, caught=confusion(y,a>0), block=confusion(y,a==2),
                scenario_recall={str(s):confusion(y[scenario==s],a[scenario==s]>0) for s in np.unique(scenario)})


def bootstrap(y, scores, days, policy, seed):
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(days==d) for d in np.unique(days)]
    samples = []
    for _ in range(500):
        idx = np.concatenate([groups[i] for i in rng.integers(len(groups),size=len(groups))])
        a = actions(scores[idx].astype(float), *policy)
        caught, block = confusion(y[idx],a>0), confusion(y[idx],a==2)
        samples.append([average_precision_score(y[idx],scores[idx]),caught["precision"],caught["recall"],
                        block["precision"],block["recall"]])
    return dict(zip(("ap","caught_precision","caught_recall","block_precision","block_recall"),
                    np.percentile(samples,[2.5,97.5],axis=0).T.tolist()))
