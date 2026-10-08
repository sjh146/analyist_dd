"""Deterministic champion retrain with an explicit train/inference feature contract.

WHY: the previous champion was trained by the strategy-experiment loop, which
trained each model on a DIFFERENT feature subset without persisting the column
order (catboost=23, lightgbm=62 positional columns, no names) while
feature_names.json was a separately sorted list. Inference fed features in json
order -> columns misaligned + ~20 features always 0.0 -> the ensemble collapsed
to a near-constant all-DOWN prediction (up:0/down:1668, hit Aug 2026).

FIX: train ALL three models on the SAME canonical feature matrix whose column
order is ``FeaturePipeline.get_feature_names()`` (a fixed sorted list) and write
``feature_names.json`` in that EXACT order. The screener builds its vector from
that same json with ``features.get(f, 0.0)``, so training and inference now use
identical positions. CatBoost additionally receives the real feature names so
its model metadata is debuggable.

Usage (in the xgboost-ml container):
    docker compose run --rm xgboost-ml python -m app.training.retrain_champion \
        --days 120 --stock-limit 200
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.catboost_model import CatBoostModel
from app.models.lightgbm_model import LightGBMModel
from app.models.xgboost_model import XGBoostModel

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "app/models/champion"


def _create_labels(df: pd.DataFrame) -> np.ndarray:
    """1-day forward close direction per stock (mirrors Trainer._create_labels)."""
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns:
        return labels
    for code in df["stock_code"].unique():
        mask = df["stock_code"] == code
        idx = df[mask].index
        prices = df.loc[idx, "price"].values.astype(np.float64)
        if len(prices) >= 2:
            next_up = prices[1:] > prices[:-1]
            vals = np.zeros(len(prices), dtype=int)
            vals[:-1] = next_up.astype(int)
            labels[idx] = vals
    return labels


def _create_labels_relative(df: pd.DataFrame, horizon: int = 5,
                            smooth: bool = False) -> np.ndarray:
    """**시장상대(중앙값)** h일 선행수익 라벨 — champion_robust_eval._make_labels(kind="rel") 과
    같은 정의다(그날 횡단면 중앙값 대비 위=1 / 아래=0).

    왜 추가하는가(2026-10-01, CG9 A단계):
      * 배포 챔피언은 **절대 1일 선행 종가 방향**(h1)으로 학습됐는데 트레이더 보유기간은 5일이고,
        승격·평가 경로(champion_robust_eval)의 라벨은 **h5 시장상대 중앙값**이다 → 학습 과제와
        배포 목표가 다르다. 이 옵션은 그 격차를 닫는 유일한 배포 가능 레버다(추론 계약 무변경).
      * 종목별 시계열 정렬은 호출자가 보장한다(df 는 이미 date 오름차순으로 정렬돼 있다).
      * smooth=True 면 t→t+1..t+h 각 시점 수익률의 **평균**을 쓴다(5일 보유와 정합, 스윕 LB_smooth).
      * 라벨이 없는 마지막 h일은 0 으로 둔다 — 기존 `_create_labels`(절대 h1)도 마지막 행을 0 으로
        두는 것과 같은 관례이며, 평가 경로는 purge 로 그 구간을 제외한다.
    """
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns or "date" not in df.columns:
        return labels
    price = df["price"].to_numpy(dtype=np.float64)
    fwd = np.full(len(df), np.nan, dtype=np.float64)
    for _code, gidx in df.groupby("stock_code", sort=False).groups.items():
        idx = np.asarray(gidx, dtype=int)
        p = price[idx]
        n = len(p)
        vals = np.full(n, np.nan, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            if smooth:
                for j in range(n):
                    ks = [k for k in range(1, horizon + 1) if j + k < n]
                    if ks:
                        vals[j] = float(np.mean([p[j + k] / p[j] - 1.0 for k in ks]))
            else:
                for j in range(n - horizon):
                    vals[j] = p[j + horizon] / p[j] - 1.0
        fwd[idx] = vals
    med = pd.Series(fwd).groupby(df["date"].to_numpy()).transform("median").to_numpy()
    ok = np.isfinite(fwd) & np.isfinite(med)
    labels[ok] = (fwd[ok] > med[ok]).astype(int)
    return labels


def _create_labels_quantile(df: pd.DataFrame, horizon: int = 5,
                            q: float = 0.05) -> np.ndarray:
    """**분위 꼬리** 라벨 — 그날 횡단면 h일 선행수익의 상위 q=1 / 하위 q=0 / 가운데 NaN.

    `wf_wave.make_labels(kind="quantile")`(스윕 프로토콜)과 **같은 정의**다:
      hi = 그날 quantile(1-q), lo = 그날 quantile(q) → ret>hi = 1, ret<lo = 0, 나머지 NaN.
    왜 필요한가(2026-10-04, CG92): 청정 패널(panel_prod200) 스윕 실측에서 라벨 꼬리
    (q0.05)가 사전문턱·실질성을 모두 통과했다(CG89 구간 짝 Δ+0.0406 5/5 · CG90 공통
    후보집합 top-k k=3 +0.0815 p=1e-4 · CG91 dose-response q0.30 0.5305 → q0.10 0.5450 →
    q0.05 0.5841). 그러나 그 셋은 전부 **스윕 프로토콜**이라, 승격 전에 배포 경로
    (retrain_champion → champion_robust_eval)로 재현해야 한다(CG9c·CG22 전례).

    ⚠ 가운데 분위는 **학습에서 제외**한다(wf_label_sweep L1518 `d=d[~isna(_y)]` 와 동일) —
    호출자가 NaN 행을 버려야 스윕과 같은 과제가 된다. 안 버리면 가운데 행이 라벨 0 으로
    학습돼 'q0.05' 가 아니라 '하위 50% 방향' 과제가 된다(조용한 정의 이탈).
    ⚠ 시점정합: 선행수익(shift(-h))만 본다. 마지막 h행은 라벨 없음(NaN).
    """
    out = np.full(len(df), np.nan, dtype=np.float64)
    if not {"stock_code", "price", "date"} <= set(df.columns):
        return out
    price = df["price"].to_numpy(dtype=np.float64)
    codes = df["stock_code"].to_numpy()
    fwd = np.full(len(df), np.nan, dtype=np.float64)
    # 위치 기반 그룹(인덱스 라벨에 의존하지 않는다 — reset_index 가 안 된 df 여도 안전).
    for _code in pd.unique(codes):
        idx = np.flatnonzero(codes == _code)
        p = price[idx]
        n = len(p)
        vals = np.full(n, np.nan, dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            for j in range(n - horizon):
                vals[j] = p[j + horizon] / p[j] - 1.0
        fwd[idx] = vals
    s = pd.Series(fwd)
    day = df["date"].to_numpy()
    hi = s.groupby(day).transform(lambda x: x.quantile(1.0 - q)).to_numpy()
    lo = s.groupby(day).transform(lambda x: x.quantile(q)).to_numpy()
    with np.errstate(invalid="ignore"):
        out[fwd > hi] = 1.0
        out[fwd < lo] = 0.0
    return out


def _add_cross_sectional_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Batch-level cross-sectional ranks, replicating
    FeaturePipeline.compute_cross_sectional_ranks / Trainer (trainer.py:107-118)."""
    rank_cols = [
        "return_5d", "return_20d", "volatility_20d",
        "volume_ratio_5", "ma_position_5", "volume_ratio_20",
    ]
    date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
    out = df.copy()
    if date_col is None:
        return out
    for col in rank_cols:
        if col in out.columns:
            out[f"rank_{col}"] = out.groupby(date_col)[col].rank(pct=True)
    return out


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _select_stocks(pg, limit: int, mode: str = "recency") -> List[str]:
    """재학습 유니버스 — ETF/ETN 제외 + (mode) 선택 (universe.py 참조).

    mode 기본 "recency" = 현행 동작 비트 동일. "liquidity" 는 CG57 실험용(일평균 거래대금 상위).

    ⚠ 유니버스 동결(2026-10-09): 기본 창은 `now − 60일` 이라 **날짜가 바뀌면 재학습 표본이
    통째로 재추첨**된다(실측: 적격 5종목 차이 → 선택 200종목 교집합 26/200). 재현이 필요하면
    `UNIVERSE_ASOF_DATE=YYYY-MM-DD` 를 설정하라 — 창이 그 날짜 기준으로 고정된다(미설정 = 현행).
    """
    from app.training.universe import select_training_universe

    return select_training_universe(pg, limit=limit, min_days=30, seed=0, mode=mode)


def retrain_champion(
    df: pd.DataFrame,
    out_dir: str,
    val_frac: float = 0.2,
    n_estimators: int = 500,
    seed: int = 0,
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
    label_kind: str = "h1_direction",
    horizon: int = 5,
    model_params: Optional[dict] = None,
    label_q: Optional[float] = None,
) -> dict:
    """Core retrain (DB-free, testable): train 3 models on ONE canonical matrix.

    label_kind(기본 "h1_direction" = **현행 동작 그대로**):
      * "h1_direction" — 절대 1일 선행 종가 방향(기존 챔피언 라벨)
      * "rel"          — h일 선행수익의 **시장상대 중앙값** 분할(평가 경로와 같은 정의)
      * "rel_smooth"   — 위 + 1~h일 수익률 평균(보유기간 정합)
      * "quantile"     — h일 선행수익의 **그날 분위 꼬리**(label_q 로 q 지정). 가운데 분위는
                         학습에서 제외된다(wf_wave.make_labels(kind="quantile") 와 같은 정의).
    model_params(기본 None = 현행): {"max_depth":1,"learning_rate":0.05} 처럼 주면 각 모델의
      params 사전에 **있는 키만** 덮어쓴다(스윕 recipe 의 depth·lr 을 생산 경로로 옮기는 통로).
    label_q(기본 None): "quantile" 일 때 필수(0<q<0.5). 다른 label_kind 에 주면 무시된다.

    Returns a summary dict with per-model val AUC, ensemble AUC, feature count.
    """
    from app.feature_engine.feature_pipeline import FeaturePipeline as _FP

    os.makedirs(out_dir, exist_ok=True)

    df = _add_cross_sectional_ranks(df)
    df = df.sort_values("date").reset_index(drop=True)
    n_middle_dropped = 0
    if label_kind == "h1_direction":
        y = _create_labels(df)
    elif label_kind in ("rel", "rel_smooth"):
        y = _create_labels_relative(df, horizon=horizon, smooth=(label_kind == "rel_smooth"))
    elif label_kind == "quantile":
        if not label_q or not (0.0 < float(label_q) < 0.5):
            raise ValueError(f"quantile 라벨은 label_q in (0, 0.5) 가 필요하다 (받은 값: {label_q!r})")
        yq = _create_labels_quantile(df, horizon=horizon, q=float(label_q))
        keep = np.isfinite(yq)
        n_middle_dropped = int((~keep).sum())
        if int(keep.sum()) < 200:
            raise ValueError(
                f"quantile 라벨 후 학습행이 {int(keep.sum())} — 표본 부족"
                f"(q={label_q}, 원 행 {len(df)})")
        df = df.loc[keep].reset_index(drop=True)
        y = yq[keep].astype(int)
    else:
        raise ValueError(f"unknown label_kind: {label_kind!r}")

    # Canonical contract: the FULL sorted feature list, same order the screener
    # builds. Missing columns are 0-filled on BOTH sides (train + inference).
    canonical: List[str] = _FP().get_feature_names()
    X = np.zeros((len(df), len(canonical)), dtype=np.float32)
    for j, name in enumerate(canonical):
        if name in df.columns:
            col = df[name].to_numpy(dtype=np.float64)
            X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)

    # Chronological split (walk-forward style: train on past, val on future).
    split = int(len(df) * (1.0 - val_frac))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    up_rate = float(y.mean())

    models = [
        ("xgboost", XGBoostModel(n_estimators=n_estimators, random_state=seed)),
        ("lightgbm", LightGBMModel(n_estimators=n_estimators, random_state=seed)),
        ("catboost", CatBoostModel(iterations=n_estimators, random_state=seed)),
    ]
    weights: dict = {}
    aucs: dict = {}
    saved: List[str] = []
    for name, model in models:
        if model_params:
            # 각 모델 params 에 **있는 키만** 덮어쓴다(없는 키는 조용히 무시 — 모델별 파라미터 이름이
            # 다르기 때문: xgb/lgb=max_depth, catboost=depth). 기본 None 이면 현행과 비트 동일.
            for _k, _v in model_params.items():
                if _k in getattr(model, "params", {}):
                    model.params[_k] = _v
        try:
            metrics = model.train(X_train, y_train, X_val, y_val)
            auc = 0.5
            if X_val is not None and len(X_val) > 10:
                try:
                    auc = roc_auc_score(y_val, model.predict(X_val))
                except Exception:
                    auc = 0.5
            aucs[name] = round(float(auc), 4)
            weights[name] = max(auc - 0.5, 0.01)
            path = os.path.join(out_dir, f"{name}_model.pkl")
            model.save(path)
            saved.append(path)
            logger.info("%s val AUC=%.4f -> saved %s", name, auc, path)
        except Exception as e:
            logger.warning("%s training failed: %s", name, e)
            aucs[name] = None

    if not saved:
        raise RuntimeError("no model trained")

    # Ensemble AUC (weighted soft-vote on val).
    ens_auc = 0.5
    if X_val is not None and len(X_val) > 10:
        probs = np.zeros(len(X_val))
        tw = 0.0
        for name, model in models:
            w = weights.get(name)
            if w is None:
                continue
            probs += w * model.predict(X_val)
            tw += w
        if tw > 0:
            try:
                ens_auc = roc_auc_score(y_val, probs / tw)
            except Exception:
                ens_auc = 0.5

    # Persist the contract + metadata.
    with open(os.path.join(out_dir, "feature_names.json"), "w") as f:
        json.dump(canonical, f)
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{ens_auc:.6f}\n")
    # 배포 실체 = **균등 평균** — EnsembleModel.load() 는 val 가중치를 복원하지 않고(그리고
    # ENSEMBLE_USE_STORED_WEIGHTS 기본 OFF), 추론은 weight=1.0 경로를 탄다. 즉 위 ens_auc(가중)는
    # 승격 게이트가 비교하는 값이지만 실제로 도는 점수가 아니다 → 두 값을 함께 남긴다.
    ens_auc_equal = ens_auc
    if X_val is not None and len(X_val) > 10:
        _eq = np.zeros(len(X_val))
        _n_eq = 0
        for name, model in models:
            if weights.get(name) is None:
                continue
            _eq += model.predict(X_val)
            _n_eq += 1
        if _n_eq > 0:
            try:
                ens_auc_equal = roc_auc_score(y_val, _eq / _n_eq)
            except Exception:
                ens_auc_equal = ens_auc
    # 가중치 파일 저장 — 배포 가중을 켤 때(승인 대상) 바로 쓸 수 있게 부산물로 남긴다.
    try:
        with open(os.path.join(out_dir, "ensemble_weights.json"), "w") as f:
            json.dump({k: float(v) for k, v in weights.items() if v is not None}, f, indent=2)
    except Exception as e:
        logger.warning("ensemble weights 저장 실패: %s", e)
    meta = {
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "n_train": int(split),
        "n_val": int(len(df) - split),
        "up_rate": round(up_rate, 4),
        "n_features": int(len(canonical)),
        "model_aucs": aucs,
        "ensemble_auc": round(float(ens_auc), 4),
        "ensemble_auc_deployed_substance": round(float(ens_auc_equal), 4),
        "ensemble_weight_usage": "equal",
        "val_frac": val_frac,
        "seed": seed,
        # 학습 데이터 구간 — champion_robust_eval 이 이 값으로 **학습구간과 겹치는 평가창을
        # 자동 제외**한다(2026-09-29 실측: 겹친 창 0.5883 vs 학습구간 밖 0.4914).
        "data_start": data_start,
        "data_end": data_end,
        # 라벨 정의 기록(2026-10-01) — 같은 모델을 두고 '무엇으로 학습했는가'를 나중에 확인할 수
        # 있어야 A/B 해석이 성립한다(CG36 교훈: 라벨 종류를 안 적어 두면 자기 과제 점수를 오독한다).
        "label_kind": label_kind,
        "horizon": int(horizon),
        "label_q": (float(label_q) if label_q is not None else None),
        # quantile 라벨에서 가운데 분위로 **버린 행 수**(스윕과 같은 과제인지 확인용).
        # 0 이 아니면 라벨 꼬리 과제다 — AUC 해석 시 '과제 정의가 다름'을 함께 적어야 한다.
        "n_middle_dropped": int(n_middle_dropped),
        "model_params_override": dict(model_params) if model_params else None,
    }
    meta_path = os.path.join(out_dir, f"training-result-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(
        "champion retrain done: n=%d up_rate=%.3f ensemble_auc=%.4f features=%d -> %s",
        len(df), up_rate, ens_auc, len(canonical), out_dir,
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic champion retrain")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--end-date", default=None,
                    help="학습 데이터 종료일(YYYY-MM-DD). 기본 None = 오늘(현행 동작). "
                         "고정하면 같은 구간으로 대조군/챌린저를 학습해 A/B 가 성립한다")
    ap.add_argument("--start-date", default=None,
                    help="학습 데이터 시작일. 기본 None = end_date - days")
    ap.add_argument("--stock-limit", type=int, default=200)
    ap.add_argument("--universe-mode", dest="universe_mode",
                    choices=("recency", "liquidity"), default="recency",
                    help="학습 표본 선택 방식. 기본 recency = 현행(최신 데이터 우선 + 시드 셔플, "
                         "실측상 무작위 표본). liquidity = 최근 60일 일평균 거래대금 상위 "
                         "(결정적 정렬 — 짝 비교용). 어느 모드든 ETF/ETN 은 제외된다.")
    ap.add_argument("--label-kind", dest="label_kind",
                    choices=("h1_direction", "rel", "rel_smooth", "quantile"),
                    default="h1_direction",
                    help="학습 라벨 정의. 기본 h1_direction = 현행(절대 1일 선행 종가 방향). "
                         "rel = h일 선행수익의 시장상대 중앙값(평가 경로 champion_robust_eval 과 "
                         "같은 정의), rel_smooth = 위 + 1~h일 수익률 평균(5일 보유 정합), "
                         "quantile = h일 선행수익의 그날 분위 꼬리(--label-q 로 q 지정; "
                         "가운데 분위는 학습에서 제외 — 스윕 wf_wave.make_labels 와 같은 정의).")
    ap.add_argument("--label-q", dest="label_q", type=float, default=None,
                    help="분위 꼬리 라벨의 q (0<q<0.5). --label-kind quantile 과 함께만 쓴다. "
                         "예: 0.05 = 상위 5%%=1 / 하위 5%%=0 / 가운데 제외. 기본 None = 현행.")
    ap.add_argument("--horizon", type=int, default=5,
                    help="rel/rel_smooth/quantile 라벨의 선행 거래일 수(기본 5 = 트레이더 보유기간)")
    ap.add_argument("--model-params", dest="model_params", default=None,
                    help='모델 params 덮어쓰기 JSON. 예: \'{"max_depth":1,"learning_rate":0.05}\'. '
                         "각 모델 params 에 있는 키만 적용된다(기본 None = 현행).")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--n-estimators", type=int, default=500)
    ap.add_argument("--checkpoint-path", default=None,
                    help="부분 진척 체크포인트 경로(컨테이너에서 접근 가능한 절대경로). 주면 "
                         "500페어마다 rows.pkl+meta.json 으로 저장하고, 종목목록·start/end·"
                         "feature_engine 코드 mtime 이 같으면 다음 실행에서 이어받는다. "
                         "왜: 200종목×90일(실측 12,029페어) 빌드는 1.26페어/s 로 2.65h 가 걸려 "
                         "저녁 파이프라인 Phase 2 의 2h 캡(timeout 7200)에 매일 잘리고 **전량 "
                         "소실**됐다(2026-09-24 exit=124 · 2026-09-29 동일). 체크포인트를 주면 "
                         "재시도가 처음부터가 아니라 이어받기로 시작한다(단, --end-date 를 고정해야 "
                         "키가 유지된다 — 매일 '오늘'로 밀리면 체크포인트도 매일 폐기된다). "
                         "기본 None = 현행 동작(체크포인트 없음).")
    args = ap.parse_args()

    # 라벨 옵션 정합(2026-10-04 CG92): quantile 은 q 가 필수이고, q 는 quantile 전용이다.
    # 조용히 무시하면 '라벨을 바꿨다'고 믿고 같은 실험을 두 번 돌린다(EV1 사고와 동형).
    if args.label_kind == "quantile" and args.label_q is None:
        ap.error("--label-kind quantile 은 --label-q <분위> 가 필요하다 (예: --label-q 0.05)")
    if args.label_q is not None and args.label_kind != "quantile":
        ap.error("--label-q 는 --label-kind quantile 과만 함께 쓴다")
    if args.label_q is not None and not (0.0 < args.label_q < 0.5):
        ap.error("--label-q 는 0 < q < 0.5 범위여야 한다")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

    pg = _pg_connect()
    try:
        stocks = _select_stocks(pg, args.stock_limit, args.universe_mode)
        logger.info("selected %d stocks", len(stocks))
        pipeline = FeaturePipeline(pg_conn=pg)
        end = (datetime.strptime(args.end_date, "%Y-%m-%d") if args.end_date
               else datetime.now())
        start = (datetime.strptime(args.start_date, "%Y-%m-%d") if args.start_date
                 else end - timedelta(days=args.days))
        start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        logger.info("학습 데이터 구간 %s ~ %s", start_s, end_s)
        df = pipeline.build_training_features(stocks, start_s, end_s,
                                               checkpoint_path=args.checkpoint_path)
        if df is None or len(df) < 500:
            logger.error("insufficient panel rows: %s", 0 if df is None else len(df))
            return
        meta = retrain_champion(df, out_dir=args.out_dir,
                                val_frac=args.val_frac, n_estimators=args.n_estimators,
                                data_start=start_s, data_end=end_s,
                                label_kind=args.label_kind, horizon=args.horizon,
                                label_q=args.label_q,
                                model_params=(json.loads(args.model_params)
                                              if args.model_params else None))
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"CHAMPION -> {os.path.abspath(args.out_dir)}")

        # 2026-08: 재학습 결과 이력 기록 (Grafana Quant Strategy Monitoring — ML AUC 패널용)
        try:
            sys.path.insert(0, "/app/scripts")
            from record_strategy_run import record_run

            record_run(
                tool="model_retrain",
                stocks=len(stocks),
                auc=float(meta.get("ensemble_auc", 0) or 0),
                metric_value=float(meta.get("ensemble_auc", 0) or 0),
                meta={
                    "model_aucs": meta.get("model_aucs", {}),
                    "n_rows": meta.get("n_rows"),
                    "n_features": meta.get("n_features"),
                    "up_rate": meta.get("up_rate"),
                },
            )
        except Exception as _e:
            print(f"[record_run] model_retrain 기록 실패(무시): {_e}")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
