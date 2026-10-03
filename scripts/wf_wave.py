#!/usr/bin/env python3
"""wf_wave — 장기 패널 빌드 + walk-forward(확장창) 교차검증.

배경: 지금까지의 AUC 는 고정 분할(60/20/20)에 따라 ±0.03 흔들렸다
(같은 설정이 0.6015 / 0.5704 / 0.5463). 또 패널이 49종목×180일 = 5,885행뿐이라
8일 호라이즌 라벨의 겹침을 감안하면 유효 표본이 더 작다.

이 스크립트는 두 가지를 한 번에 한다.
  1) 장기 패널 빌드(--days 420, 기본 캐시 /app/app/models/wf/panel_420.npz)
  2) 확장창 walk-forward: 날짜를 (folds+1)개 블록으로 나눠
     fold i → 학습 = 블록 0..i, 테스트 = 블록 i+1 (경계 h거래일 purge)
     각 fold 에서 피처 선택은 **그 fold 의 학습 구간에서만** 계산한다.

판정 기준은 fold 평균 AUC(그리고 fold 간 표준편차)다. 단일 분할 값은 쓰지 않는다.

실행(컨테이너):
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_wave.py            # 본 실행(2~3시간)
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_wave.py --smoke    # 경로 검증
결과: /app/reports/overnight/wf_wave.jsonl + wf_wave_summary.json
"""

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402

ml = ex._load_driver()
_ORIG_SELECT = tc.select_curated_features

BASE = {"horizon": 8, "q": 0.3, "select": "top30",
        "recipe": {"lr": 0.03, "depth": 4, "n_estimators": 1500}}

CONFIGS = [
    {"id": "WF1", **BASE, "desc": "승자: h8 + 분위0.3 + top30"},
    {"id": "WF2", **BASE, "select": "top40", "desc": "h8 + 분위0.3 + top40"},
    {"id": "WF3", **BASE, "horizon": 5, "desc": "h5 + 분위0.3 + top30"},
    {"id": "WF4", **BASE, "horizon": 6, "select": "top40", "desc": "h6 + top40 (분할 편차 최소)"},
    {"id": "WF5", **BASE, "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "h8 + lr0.02 d3 est2000"},
]


def edge_of(col, y):
    m = ~np.isnan(col)
    x, yy = col[m], y[m]
    if len(x) < 50 or len(np.unique(x)) < 2 or yy.min() == yy.max():
        return 0.0
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1, n0 = int(yy.sum()), len(yy) - int(yy.sum())
    if n1 == 0 or n0 == 0:
        return 0.0
    return abs((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0) - 0.5)


def _rankdata(a):
    """평균 순위(동점 = 중간 순위). spearman 을 scipy 없이 계산하기 위한 헬퍼."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    xs = a[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def _spearman(x, y):
    rx = _rankdata(x)
    ry = _rankdata(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    d = float(np.sqrt((rx ** 2).sum() * (ry ** 2).sum()))
    return 0.0 if d == 0 else float((rx * ry).sum() / d)


def ic_scores(X, y, dates, min_dates=10, min_rows=8):
    """**날짜별 횡단면 순위 IC** 기반 피처 점수 (선별 *규칙* 축).

    왜 edge_of 로는 부족한가: edge_of 는 폴드 학습구간을 **풀링**한 순위 AUC 다 →
    ① 날짜 레벨 성분(그 날 전체가 좋았는가)이 지배할 수 있고 ② 소수 날짜/레짐이
    edge 를 만들 수 있다. 모델은 **날짜별 횡단면 순위**로 채점되므로(그 날 안에서만
    종목을 비교) 선별 목적함수를 채점 목적함수에 맞추는 것이 이 축의 가설이다.
    점수 = |평균 IC| × 일관성(다수 방향 날짜 비율) — 부호가 날마다 뒤집히는 피처를
    평균만으로 뽑지 않기 위한 항.
    ⚠ 학습행·학습라벨만 받는다(평가행 미사용 = 누수 없음). 관측날짜 < min_dates → 0.
    반환: (scores ndarray, 관측날짜수 ndarray)
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y).astype(int)
    d = np.asarray(dates).astype(str)
    _, di = np.unique(d, return_inverse=True)
    di = np.asarray(di).reshape(-1)
    groups = [np.where(di == g)[0] for g in range(int(di.max()) + 1)]
    ncols = X.shape[1]
    scores = np.zeros(ncols, dtype=float)
    nds = np.zeros(ncols, dtype=int)
    for c in range(ncols):
        col = X[:, c]
        ics = []
        for rows in groups:
            x, yy = col[rows], y[rows]
            m = ~np.isnan(x)
            if int(m.sum()) < min_rows:
                continue
            x, yy = x[m], yy[m]
            if len(np.unique(x)) < 2 or yy.min() == yy.max():
                continue
            ics.append(_spearman(x, yy))
        if len(ics) < min_dates:
            continue
        arr = np.asarray(ics, dtype=float)
        mean = float(arr.mean())
        cons = max(float((arr > 0).mean()), float((arr < 0).mean()))
        scores[c] = abs(mean) * cons
        nds[c] = len(arr)
    return scores, nds


def subset(names, select, X_train, y_train, dates=None):
    if select == "all":
        return list(range(len(names))), "all"
    if select.startswith("curated"):
        keep = set(_ORIG_SELECT(names, select.endswith("48")))
        return [i for i, n in enumerate(names) if n in keep], select
    if select.startswith("ic"):
        # ⚠ dates 없이 돌리면 **다른 규칙(풀링 edge)으로 조용히 바뀐다** → 즉시 실패시킨다.
        if dates is None:
            raise RuntimeError(
                "select=%s 는 학습행 날짜(dates=)를 요구한다 — 날짜별 횡단면 IC 선별이라 "
                "날짜 없이 호출하면 선별 규칙이 달라진다(조용한 폴백 금지)." % select)
        k = int(select.replace("ic", ""))
        sc, _nds = ic_scores(X_train, y_train, dates)
        idx = sorted(int(i) for i in np.argsort(-sc)[:k])
        # 투명성: 풀링 edge top-k 와 몇 개가 겹치는지 남긴다. 선별 규칙이 **실제로 다른
        # 집합**을 고르는지 확인하지 않으면 'Δ0' 을 '효과 없음'으로 오독하게 된다(EV1 사고).
        edges = np.array([edge_of(X_train[:, i].astype(float), y_train)
                          for i in range(len(names))])
        e_top = {int(i) for i in np.argsort(-edges)[:k]}
        return idx, f"ic{k} (edge top{k} 와 {len(e_top & set(idx))}/{k} 겹침)"
    if select.startswith("cnd"):
        # ── 조건부 edge 선별 (2026-10-03 CG76) ─────────────────────────────────
        # 왜: `edge_of` 는 폴드 학습구간을 **풀링**한 순위 AUC 라, 희소 피처는 전 행의 대부분이
        # 0 이어서 |AUC−0.5| 가 0 에 가까워 top-k 에 절대 들어가지 못한다. 실측 근거:
        #   · EV1(2026-09-26) 이벤트 17종 진입 0개 → EV_all == EV_none 비트 동일
        #   · CG67(2026-10-02) disclosure_count_5d 를 42.2% 커버리지로 부활해도 선별 진입 0 →
        #     AUC 불변(0.5350/0.5512 — CG64/CG66 과 소수점 동일)
        # 즉 '피처가 죽어서'가 아니라 **선별 규칙이 희소 피처를 후보에서 탈락시켜서** 모델에 안 들어간다.
        # 이 분기는 비영 행에서만 edge 를 계산해 희소 피처가 dense 피처와 경쟁하게 한다.
        # 비영 표본이 min_nz 미만이면 후보 제외(추정 불가·노이즈 방지). 비영=전 행(dense)이면
        # 풀링 edge 와 값이 같으므로 **축은 희소 피처에만 작용**한다(다른 축과 교락 없음).
        # 반환 desc 에 풀링 edge top-k 와의 겹침을 남겨 '규칙이 실제로 다른 집합을 골랐는지'를
        # 확인 가능하게 한다 — 0 이면 같은 실험을 두 번 돌린 것(EV1 사고)이다.
        k = int(select.replace("cnd", ""))
        Xf = X_train.astype(float)
        yy = np.asarray(y_train).astype(int)
        n_rows = Xf.shape[0]
        min_nz = max(50, int(round(0.005 * n_rows)))   # edge_of 는 len>=50 을 요구한다
        ncols = Xf.shape[1]
        sc = np.zeros(ncols, dtype=float)
        for i in range(ncols):
            col = Xf[:, i]
            nz = ~np.isnan(col) & (col != 0)
            if int(nz.sum()) < min_nz:
                continue                                # 너무 희소 → 후보 제외
            sc[i] = edge_of(col[nz], yy[nz])            # 비영 행에서만 edge
        idx = sorted(int(i) for i in np.argsort(-sc)[:k])
        pool = np.array([edge_of(Xf[:, i], yy) for i in range(ncols)])
        e_top = {int(i) for i in np.argsort(-pool)[:k]}
        return idx, f"cnd{k} (min_nz={min_nz}, edge top{k} 와 {len(e_top & set(idx))}/{k} 겹침)"
    edges = np.array([edge_of(X_train[:, i].astype(float), y_train)
                      for i in range(len(names))])
    k = int(select.replace("top", ""))
    order = np.argsort(-edges)[:k]
    return sorted(int(i) for i in order), f"top{k}"


def dedupe_names(names):
    """패널 피처명의 **중복 라벨을 제거**한다(두 번째부터 `__dupN` 접미사).

    ⚠ 왜 필수인가 (실측 2026-09-25, panel_420_asofpatch.npz):
      피처명 210개 중 14개가 중복(cross_trend, price_volume, target_ma_5, volume_price_trend…)이라
      `df[names]` 가 열을 **238개로 부풀리고 순서를 바꾼다**. 그래서
      `Xtr = transform_matrix(tr[base_names])` 의 열과 `base_names`(이름 목록)가 어긋나고,
      선별 인덱스 `Xtr[:, idx]` 가 **다른 열**을 학습에 넣는다 →
      '기록된 피처 이름'과 '실제 학습 열'이 달라져 **피처 단위 판정이 전부 무효**가 된다.
      증상은 조용하다(예외 없음, AUC 도 정상값). 그래서 이름을 1:1 로 만들어 뿌리에서 막는다.
    """
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}__dup{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def select_panel_codes(pg, limit, universe="curated", universe_seed=0, log=print,
                       **universe_opts):
    """패널 유니버스 코드 선택 — 실험(curated) vs **프로덕션(prod)** 경로 정렬용.

    왜(2026-09-28, CG10/CG9): 지금까지 모든 스윕은 `train_curated._select_universe`
    (KOSDAQ·코드순·최소 50일)로 49~150종목을 골랐는데, **프로덕션 챔피언은
    `app.training.universe.select_training_universe(limit=200, min_days=30, seed=0)`
    = ETF/ETN 제외 + 최근 데이터 순 + seed 셔플** 로 200종목을 고른다. 유니버스가 다르면
    스윕에서 잰 Δ 가 승격 조건을 대표하지 못한다(CG5: 같은 config 가 49종목 +0.0227 →
    150종목 −0.0109 로 부호 반전).

    universe:
      "curated"(기본·현행) — tc._select_universe(limit, market/since/min_days/min_value/order)
      "prod"               — 프로덕션 학습기와 **같은 함수**로 선택(옵션은 무시되고 로그로 알린다).
                             ⚠ prod 는 KOSPI+KOSDAQ 혼합이므로 패널 파일명을 반드시 새로 써라
                             (예: panel_prod200.npz) — 기존 패널을 덮으면 대조군이 사라진다.
    """
    if universe in (None, "curated"):
        return tc._select_universe(pg, limit, **universe_opts)
    if universe != "prod":
        raise ValueError(f"unknown universe={universe!r} (curated|prod)")

    from app.training.universe import select_training_universe
    if universe_opts:
        log(f"⚠ --universe prod: curated 옵션 {sorted(universe_opts)} 는 무시된다 "
            f"(프로덕션 규칙 = ETF/ETN 제외·최근데이터순·min_days=30·seed={universe_seed})")
    codes = select_training_universe(pg, limit=limit, min_days=30, seed=universe_seed)
    return codes


def build_panel(cache, limit, days, log=print, end_date=None, universe="curated",
                universe_seed=0, **universe_opts):
    """패널 캐시를 만들거나 재사용한다.

    end_date: 빌드 구간의 끝 날짜(YYYY-MM-DD). 기본 None = 실행 시각(now).
      ⚠ 왜 필요한가(실측 2026-09-28): 구간이 `end=now` 로 매일 하루씩 밀리므로 **체크포인트가
      날마다 무효화**된다(feature_pipeline 비교 키 = stock_codes·start_date·end_date·code_sig).
      995일 창(32,576 페어·실측 0.368 pair/s = 24.6시간)을 여러 밤에 걸쳐 완주하려면 구간을
      고정해야 한다 — 안 그러면 매일 0% 에서 다시 시작해 영원히 완주하지 못한다(47%에서 두 번 소실).
      고정하면 재개가 실제로 이어지고, 구간이 같으므로 실험 프로토콜도 그대로다.

    universe: "curated"(기본·현행) | "prod"(프로덕션 select_training_universe 와 동일 규칙).
    나머지 universe_opts: `tc._select_universe` 로 전달되는 확장 옵션
      (market/since/min_days/min_value/order). 비우면 현행 기본값(KOSDAQ·코드순·최소 50일).
    ⚠ 캐시는 **파일명으로만** 구분된다 → 유니버스를 바꾸면 반드시 새 파일명을 써라
      (예: --panel /app/app/models/wf/panel_500.npz). 기존 패널을 덮으면 대조군이 사라진다.
    """
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        names = dedupe_names([str(n) for n in z["feature_names"]])
        Xc = z["X"]
        if Xc.shape[1] != len(names):
            raise RuntimeError(
                f"패널 파일 불일치: X 열 {Xc.shape[1]}개 vs 피처명 {len(names)}개 ({cache}) "
                f"— 이름↔열 매핑이 깨진 파일이다(중복 라벨로 저장된 흔적). 재빌드하라.")
        df = pd.DataFrame(Xc, columns=names)
        df["date"] = [str(d) for d in z["dates"]]
        df["stock_code"] = [str(c) for c in z["codes"]]
        df["price"] = z["price"].astype(float)
        log(f"panel cache 재사용: {df.shape} ({cache})")
        return df, names

    sig_at_start = ml.FeaturePipeline._feature_code_sig()
    pg = ml.connect_pg()
    try:
        codes = select_panel_codes(pg, limit, universe=universe, universe_seed=universe_seed,
                                   log=log, **universe_opts)
        log(f"universe[{universe}]: {len(codes)} 종목 (limit={limit})")
        pipeline = ml.FeaturePipeline(pg_conn=pg)
        end = (datetime.strptime(end_date, "%Y-%m-%d") if end_date
               else datetime.now())
        start = end - timedelta(days=days)
        log(f"빌드 구간: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
            + (" (end_date 고정 — 체크포인트 재개용)" if end_date else ""))
        df = pipeline.build_training_features(
            codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
            # 체크포인트: 부분 진척을 저장/재개한다. 컨테이너 재생성으로 docker exec 가
            # SIGKILL 되어도(실측 2026-09-25: 30,000/41,893 에서 전량 소실) 다음 실행이 이어받는다.
            checkpoint_path=cache,
            checkpoint_every=int(os.environ.get("PANEL_CK_EVERY", "500")))
        if df is None or len(df) < 100:
            raise RuntimeError("panel build failed")
        base = pipeline.get_feature_names()
        df, available = ml._engineer_features(df, base)
        log(f"panel: {df.shape} features={len(available)}")
    finally:
        try:
            pg.close()
        except Exception:
            pass

    # ⚠ 빌드 중 피처 코드가 바뀌면 **저장하지 않는다**: 앞부분(옛 코드)과 뒷부분(새 코드) 행이
    # 섞이면 결측 패턴이 종목/기간과 상관돼 '종목 식별 증폭' 같은 유사누수가 생긴다(이 역할의
    # 실측 교훈). 리서처가 병행 편집 중일 때 9시간 빌드를 통째로 날리는 대신 명시적으로 실패시킨다.
    sig1 = ml.FeaturePipeline._feature_code_sig()
    if sig_at_start is not None and sig1 is not None and sig_at_start != sig1:
        for suf in (".rows.pkl", ".meta.json"):
            try:
                os.remove(cache + suf)      # 오염된 체크포인트는 버린다(깨끗한 재빌드 유도)
            except OSError:
                pass
        raise RuntimeError(
            f"빌드 중 피처 코드 변경 감지(code_sig {sig_at_start} → {sig1}) — 혼합 패널 방지를 위해 "
            f"저장하지 않음. 피처 작업이 멈춘 뒤 다시 실행하라.")

    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.savez_compressed(
        cache, X=df[available].values.astype(np.float32),
        feature_names=np.array(available),
        dates=df["date"].astype(str).values,
        codes=df["stock_code"].astype(str).values,
        price=df["price"].values.astype(np.float64))
    log(f"panel cache 저장: {cache}")
    # 성공했으면 체크포인트는 지운다(다음 유니버스 실행이 옛 진척을 물려받지 않도록).
    for suf in (".rows.pkl", ".meta.json"):
        try:
            os.remove(cache + suf)
        except OSError:
            pass
    return df, available


def make_labels(df, kind, horizon, q):
    """라벨 생성. kind: quantile(기본) / relative(시장상대) / smooth / voladj / voladj_smooth.

    ⚠ 시점정합: 모든 변형은 **앞만** 본다(선행수익 shift(-k), 후행변동성 rolling).
    smooth   = 선행 1~h일 수익률의 평균 — 5일 보유와 정합, 라벨 잡음 축소.
    voladj   = 선행 h일 수익률 ÷ 후행 20일 실현변동성(위험조정, 표준 관행).
    """
    price = df.groupby("stock_code", sort=False)["price"]
    if kind in ("smooth", "voladj_smooth"):
        acc = None
        for k in range(1, horizon + 1):
            r = price.transform(lambda s, _k=k: s.shift(-_k) / s - 1.0)
            acc = r if acc is None else acc + r
        ret = acc / float(horizon)
    else:
        ret = price.transform(lambda s: s.shift(-horizon) / s - 1.0)
    if kind in ("voladj", "voladj_smooth"):
        vol = price.transform(
            lambda s: s.pct_change().rolling(20, min_periods=10).std())
        # 변동성 0/결측 → 라벨 결측(보수적: 추정 불가 구간은 학습에서 제외된다).
        ret = ret / vol.replace(0.0, np.nan)
    day = df["date"]
    if kind == "relative":
        med = ret.groupby(day).transform("median")
        y = (ret > med).astype(float)
        y[ret.isna()] = np.nan
        return y.values
    hi = ret.groupby(day).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(day).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y.values


def universe_report(limit=200, seed=0):
    """유니버스 배선 검증 리포트 — **DB 조회만** 한다(패널 빌드 없음).

    왜(2026-09-28, CG10 선행조건): 스윕 유니버스(curated: KOSDAQ·코드순·최소 50일)와 프로덕션
    학습 유니버스(prod: ETF/ETN 제외·최근데이터순·200종목·seed 셔플)가 실제로 어떻게 다른지를
    DB 수준에서 먼저 확인한다. 15시간짜리 패널 빌드를 띄우기 전에 'KOSPI 가 섞여 나오는가 ·
    교집합이 얼마인가'를 수 초에 판정하는 것이 목적이다.
    """
    pg = ml.connect_pg()
    out = {}
    try:
        for name in ("curated", "prod"):
            codes = select_panel_codes(pg, limit, universe=name, universe_seed=seed, log=ml.log)
            cur = pg.cursor()
            cur.execute(
                "SELECT market, COUNT(*) FROM stocks WHERE stock_code = ANY(%s) GROUP BY market",
                (list(codes),))
            mix = {m: int(c) for m, c in cur.fetchall()}
            cur.execute(
                "SELECT COUNT(*) FROM stocks WHERE stock_code = ANY(%s) AND instrument_type <> 'STOCK'",
                (list(codes),))
            non_stock = int(cur.fetchone()[0])
            cur.close()
            out[name] = {"n": len(codes), "market_mix": mix, "non_stock": non_stock,
                         "codes": list(codes)}
            ml.log(f"[{name}] n={len(codes)} 시장={mix} 비주식={non_stock}")
    finally:
        try:
            pg.close()
        except Exception:
            pass

    a = set(out["curated"]["codes"])
    c = set(out["prod"]["codes"])
    inter = a & c
    ml.log(f"교집합 {len(inter)}/{limit} ({len(inter) / max(1, limit) * 100:.1f}%) "
           f"· curated 전용 {len(a - c)} · prod 전용 {len(c - a)}")
    out["overlap"] = {"n": len(inter), "ratio": round(len(inter) / max(1, limit), 4)}
    out["kospi_in_prod"] = int(out["prod"]["market_mix"].get("KOSPI", 0))
    print(json.dumps({k: v for k, v in out.items()}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=420)
    # 구간 끝을 고정한다(기본: 실행 시각). 긴 패널 빌드를 여러 밤에 걸쳐 재개하려면 필수 —
    # end_date 가 매일 밀리면 체크포인트가 매번 무효화된다(wf_wave.build_panel 주석 참조).
    ap.add_argument("--end-date", default=None,
                    help="빌드 구간 끝 날짜 YYYY-MM-DD (기본: 실행 시각)")
    ap.add_argument("--limit", type=int, default=50)
    # ── 유니버스 경로 (2026-09-28 CG10 배선) ────────────────────────────────────
    # curated(기본·현행 = train_curated._select_universe) vs prod(프로덕션 챔피언 학습기와
    # **같은 함수** app.training.universe.select_training_universe: ETF/ETN 제외·최근데이터순·
    # 200종목·seed 셔플). 기본값은 현행 유지 — 기존 패널·기준선의 재현성이 깨지면 안 된다.
    ap.add_argument("--universe", default="curated", choices=["curated", "prod"],
                    help="curated(현행 기본) | prod(프로덕션 200종목 규칙)")
    ap.add_argument("--universe-seed", type=int, default=0,
                    help="prod 유니버스 셔플 시드(프로덕션 학습기와 동일하게 0)")
    ap.add_argument("--universe-report", action="store_true",
                    help="패널을 빌드하지 않고 **유니버스 코드만** 조회해 보고(DB 전용 검증)")
    # ── 캐시 파일명 (2026-09-28 CG10 배선) ──────────────────────────────────────
    # 캐시는 **파일명으로만** 구분된다. 기본값은 현행(panel_{days}.npz)이지만, 유니버스를
    # 바꾼 실행은 반드시 새 파일명을 써야 한다 — 안 그러면 기준선 패널을 덮어 **대조군이
    # 사라진다**(과거 U1 계열에서 실제로 위험했던 지점). 그래서 prod + 기본 파일명은 거부한다.
    ap.add_argument("--panel", default=None,
                    help="패널 캐시 경로 (기본 /app/app/models/wf/panel_{days}.npz)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--smoke", action="store_true")
    # 요약 JSON 경로를 지정할 수 있게 한다(기본 /app/reports/overnight/wf_wave_summary.json).
    # 왜(2026-10-03): 구동기 `summary_path("wf_wave_summary", cmd)` 가 고정 경로를 쓰므로, 여러
    # wf_wave 런을 서로 다른 파일로 남기려면 이 플래그가 필요하다(같은 파일을 덮어 다른 실험의
    # 산출물을 잃는 것을 막는다 — champion_robust_eval 의 --out 과 같은 이유).
    ap.add_argument("--summary-out", default=None,
                    help="요약 JSON 경로 (기본 /app/reports/overnight/wf_wave_summary.json)")
    args = ap.parse_args()

    if args.universe_report:
        # DB 조회만으로 유니버스 배선을 검증한다(패널 빌드 없이 수 초) — CG10 의 선행 조건.
        ml.set_exp_log("wf_universe_report")
        return universe_report(args.limit, args.universe_seed)

    if args.smoke:
        args.days, args.limit, args.folds, args.seeds = 90, 8, 2, 1
        cache = "/app/app/models/wf/panel_smoke.npz"
        results_path = "/app/reports/overnight/wf_wave_smoke.jsonl"
        summary_path = "/app/reports/overnight/wf_wave_smoke_summary.json"
        cfgs = [dict(CONFIGS[0], horizon=3, select="top10")]
    else:
        cache = args.panel or f"/app/app/models/wf/panel_{args.days}.npz"
        # prod 유니버스 + 기본 파일명 조합은 **기준선 패널 덮어쓰기** 사고를 낸다 → 거부.
        if args.universe == "prod" and not args.panel:
            print("거부: --universe prod 는 --panel 새 경로가 필수다 "
                  "(기본값이면 기준선 패널을 덮어 대조군이 사라진다). "
                  "예: --panel /app/app/models/wf/panel_prod200.npz")
            return 2
        results_path = "/app/reports/overnight/wf_wave.jsonl"
        summary_path = args.summary_out or "/app/reports/overnight/wf_wave_summary.json"
        cfgs = CONFIGS

    ml.set_exp_log("wf_wave")
    ml.log(f"wf_wave start KST={ml.now_kst().isoformat(timespec='seconds')} "
           f"days={args.days} limit={args.limit} folds={args.folds} seeds={args.seeds}")
    df, names = build_panel(cache, args.limit, args.days, log=ml.log,
                            end_date=args.end_date, universe=args.universe,
                            universe_seed=args.universe_seed)
    base_names = [n for n in names if n in df.columns]
    all_dates = sorted(df["date"].astype(str).unique())
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} "
           f"({all_dates[0]} ~ {all_dates[-1]})")

    tc.select_curated_features = lambda n, a=False: list(n)

    results = []
    for cfg in cfgs:
        exp_id = cfg["id"]
        recipe = cfg.get("recipe", BASE["recipe"])
        out_dir = os.path.join("/app/app/models/wf", exp_id)
        os.makedirs(out_dir, exist_ok=True)
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(),
               "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = make_labels(df, "quantile", cfg["horizon"], cfg["q"])
            d = df.copy()
            d["_y"] = y
            # 라벨 참조일 기준 purge(실측 2026-09-25: 달력 h일 purge 는 갭 종목의 학습 라벨이
            # 테스트 구간 가격을 참조하는 행을 남긴다 — wf_label_sweep 에서 5폴드 22행 확인).
            d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-cfg["horizon"])
            d = d[~pd.isna(d["_y"])]
            dd = sorted(d["date"].astype(str).unique())
            n = len(dd)
            step = n // (args.folds + 1)
            fold_means, fold_ens = [], []
            for i in range(1, args.folds + 1):
                cut = dd[step * i - 1]
                nxt = dd[min(n - 1, step * (i + 1) - 1)]
                h = cfg["horizon"]
                purge = set(dd[max(0, step * i - h):step * i])
                tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
                if "_ref" in tr.columns:
                    _bad = np.greater_equal(np.asarray(tr["_ref"].astype(str).values),
                                            np.asarray(dd[step * i]))
                    if _bad.any():
                        ml.log(f"  {exp_id} fold{i}: 라벨 참조일 purge {int(_bad.sum())}행 제거")
                        tr = tr[np.logical_not(_bad)]
                te = d[(d["date"] > cut) & (d["date"] <= nxt)]
                if min(len(tr), len(te)) < 100:
                    ml.log(f"  {exp_id} fold{i}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                Xtr = np.nan_to_num(tr[base_names].values.astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(te[base_names].values.astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                cols = np.std(Xtr, axis=0) > 0
                fn = [f for f, m in zip(base_names, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                idx, sel_desc = subset(fn, cfg["select"], Xtr, ytr)
                sel = [fn[j] for j in idx]
                aucs, probs = [], []
                for seed in range(args.seeds):
                    a, m_aucs, cur, ens = ml.train_seed(
                        Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                        out_dir, seed, recipe["lr"], recipe["depth"],
                        recipe["n_estimators"], True, None)
                    aucs.append(float(a))
                    try:
                        p = np.asarray(ens.predict(Xte[:, idx]), dtype=float)
                        probs.append(p[:, -1] if p.ndim > 1 else p)
                    except Exception:
                        pass
                ens_auc = None
                if probs:
                    from sklearn.metrics import roc_auc_score
                    ens_auc = float(roc_auc_score(yte, np.mean(np.vstack(probs), axis=0)))
                fold_means.append(float(np.mean(aucs)))
                if ens_auc:
                    fold_ens.append(ens_auc)
                rec["folds"][f"fold{i}"] = {
                    "train_rows": int(len(ytr)), "test_rows": int(len(yte)),
                    "test_from": str(te["date"].min()), "test_to": str(te["date"].max()),
                    "mean": float(np.mean(aucs)), "std": float(np.std(aucs, ddof=1)),
                    "ens_pred_auc": ens_auc, "n_features": len(sel),
                    "up_rate": float(np.mean(yte))}
                ml.log(f"  {exp_id} fold{i}: train={len(ytr)} test={len(yte)} "
                       f"({te['date'].min()}~{te['date'].max()}) mean={np.mean(aucs):.4f} "
                       f"ens={ens_auc if ens_auc is None else round(ens_auc, 4)} "
                       f"feat={len(sel)}")
            if fold_means:
                rec.update({"status": "ok", "fold_mean": float(np.mean(fold_means)),
                            "fold_std": float(np.std(fold_means, ddof=1)),
                            "fold_min": float(np.min(fold_means)),
                            "fold_max": float(np.max(fold_means)),
                            "ens_mean": float(np.mean(fold_ens)) if fold_ens else None,
                            "n_folds_run": len(fold_means)})
                ml.log(f"RESULT {exp_id} fold평균={rec['fold_mean']:.4f} "
                       f"±{rec['fold_std']:.4f} (min {rec['fold_min']:.4f} / "
                       f"max {rec['fold_max']:.4f}) ens평균="
                       f"{rec['ens_mean'] if rec['ens_mean'] is None else round(rec['ens_mean'], 4)}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(results_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r["fold_mean"]) if ok else None
    with open(summary_path, "w") as f:
        json.dump({"finished_at": ml.now_iso(), "config": vars(args),
                   "best": best, "results": results}, f, ensure_ascii=False, indent=2)
    ml.log(f"wf_wave done. best={best['exp'] if best else None} "
           f"fold_mean={best['fold_mean'] if best else None}")
    print(json.dumps({"all": [{"exp": r["exp"], "fold_mean": r.get("fold_mean"),
                               "fold_std": r.get("fold_std"),
                               "fold_min": r.get("fold_min"),
                               "fold_max": r.get("fold_max"),
                               "ens_mean": r.get("ens_mean"),
                               "desc": r["desc"]} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
