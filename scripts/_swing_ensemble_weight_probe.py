#!/usr/bin/env python3
"""진단 프로브: 배포 추론의 앙상블 가중치가 '검증된' 가중치와 같은가 (MT116).

배경(코드 실측): `app/models/ensemble_model.py` 의 `EnsembleModel.train()` 은 서브모델
val AUC 로 `val_weights = max(auc-0.5, 0.01)` 을 만들지만, `load()` 는 그 가중치를
디스크에서 복원하지 않는다(모델 디렉터리에 가중치 파일 자체가 없다) → `predict()` 의
`self.val_weights.get(name, 1.0)` 이 전부 1.0 이 되어 **추론은 균등 평균**이 된다.
반면 `robust_auc.json` 의 `ensemble_auc`(검증값)는 가중 평균 기준이다.

이 프로브는 같은 유니버스·같은 피처행렬 위에서
  (a) 배포 경로(균등 평균, EnsembleModel.predict)
  (b) 검증 경로(robust_auc.json 의 model_aucs 로 가중 평균)
를 모델 디렉터리별로 비교해, 후보 문턱 0.55 를 넘는 신호가 (a)에서만 사라지는지 본다.

읽기 전용 — DB 쓰기 없음. 실행(컨테이너):
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_swing_ensemble_weight_probe.py
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402

import swing_screener as ss  # noqa: E402
from app.models.ensemble_model import EnsembleModel  # noqa: E402

CONF_TS = 0.55
MODEL_DIRS = [
    ("champion(배포,auc.txt=0.554776)", "/app/app/models/champion"),
    ("champion_prev_20261001-122646(직전 배포)", "/app/app/models/champion_prev_20261001-122646"),
    ("cand_cg51(배포가능 후보)", "/app/app/models/cand_cg51"),
]
# 릴리스 사전검사(tools/release_precheck.py --with-probe)가 후보/챔피언 쌍을 넘길 때 사용:
#   PROBE_MODEL_DIRS='[["candidate","/app/app/models/champion_cand"],["champion","/app/app/models/champion"]]'
_env_dirs = os.environ.get("PROBE_MODEL_DIRS")
if _env_dirs:
    MODEL_DIRS = [(str(a), str(b)) for a, b in json.loads(_env_dirs)]


def weights_from(dirpath, names):
    """robust_auc.json 의 model_aucs → train() 과 같은 식의 가중치."""
    fp = os.path.join(dirpath, "robust_auc.json")
    if not os.path.exists(fp):
        return None, {}
    meta = json.load(open(fp))
    m = meta.get("model_aucs") or {}
    w = {n: max(float(m.get(n, 0.5)) - 0.5, 0.01) for n in names if n in m}
    return meta, w


def stats(p, label):
    p = np.asarray(p, dtype=float)
    return (
        f"    {label:<34} n={len(p)} max={p.max():.4f} p95={np.percentile(p, 95):.4f} "
        f"median={np.median(p):.4f} mean={p.mean():.4f} "
        f">0.55:{int((p > CONF_TS).sum())} >0.50:{int((p > 0.5).sum())}"
    )


def main():
    t0 = time.time()
    pg = ss.get_pg_conn()
    stocks = ss.get_kosdaq_stocks(pg)
    print(f"[universe] KOSDAQ {len(stocks)} 종목 (screener 와 동일 필터)", flush=True)
    pipeline = ss.FeaturePipeline(pg_conn=pg)
    feats = {}
    for i, (code, name, sector, latest) in enumerate(stocks):
        try:
            f = pipeline.build_features(code, str(latest))
            if f and f.get("feature_count", 0) >= 10:
                feats[code] = f
        except Exception:
            continue
        if (i + 1) % 100 == 0:
            print(f"[build] {i + 1}/{len(stocks)} ({time.time() - t0:.0f}s)", flush=True)
    pipeline.compute_cross_sectional_ranks(feats)
    codes = list(feats.keys())
    print(f"[build] 피처 {len(codes)} 종목 완료 {time.time() - t0:.0f}s", flush=True)
    summary = {}

    for label, d in MODEL_DIRS:
        print(f"\n=== {label}  [{d}]", flush=True)
        ens = EnsembleModel(model_dir=d)
        ens.load(d)
        fn = os.path.join(d, "feature_names.json")
        names = json.load(open(fn))
        X = np.array([[float(feats[c].get(k, 0.0)) for k in names] for c in codes], dtype=np.float32)
        X = np.nan_to_num(X)
        meta, wts = weights_from(d, ens.model_names)
        probs = {}
        for nm, m in zip(ens.model_names, ens.models):
            try:
                probs[nm] = np.asarray(m.predict(X), dtype=float).ravel()
            except Exception as e:  # noqa: BLE001
                print(f"    !! 서브모델 {nm} 실패: {type(e).__name__}: {e}", flush=True)
        print(f"    피처 {len(names)}개 · 가중치(검증식)={ {k: round(v, 4) for k, v in wts.items()} }", flush=True)
        print(f"    robust_auc.json ensemble_auc={meta.get('robust_auc') if meta else None}", flush=True)
        for nm, p in probs.items():
            print(stats(p, f"submodel:{nm}"), flush=True)

        deployed = np.asarray(ens.predict(X), dtype=float).ravel()   # 균등 평균 (배포 경로)
        print(stats(deployed, "DEPLOYED(균등평균)"), flush=True)
        summary[label] = {"n": int(len(deployed)), "deployed_max": float(deployed.max()),
                          "deployed_gt_conf": int((deployed > CONF_TS).sum())}
        if wts and len(probs) == len(wts):
            num = sum(probs[n] * w for n, w in wts.items())
            den = sum(wts.values())
            weighted = num / den
            print(stats(weighted, "WEIGHTED(검증식 가중평균)"), flush=True)
            order = np.argsort(-weighted)[:5]
            print("    WEIGHTED top5: " + ", ".join(
                f"{codes[i]} {weighted[i]:.4f}" for i in order), flush=True)
            order2 = np.argsort(-deployed)[:5]
            print("    DEPLOYED top5: " + ", ".join(
                f"{codes[i]} {deployed[i]:.4f}" for i in order2), flush=True)
            r = np.corrcoef(deployed, weighted)[0, 1]
            print(f"    상관(순위 유사도)={r:.4f}", flush=True)
    pg.close()
    print("PROBE_SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"\n[done] {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
