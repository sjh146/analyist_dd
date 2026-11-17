#!/usr/bin/env python3
"""피처 생존 판정기 — 패널만 읽어 3축(분산·커버리지·누수)으로 피처별 생존 등급을 매긴다.

(스펙: docs/spec_feature_survival.md, 사용자 승인 "응 1로 진행해줘" 2026-11-17)
읽기 전용: DB·네트워크·docker 미사용, numpy/pandas 만 쓴다(호스트 /usr/bin/python3 실행).

WHY — 판정 이력이 창 정의에 따라 뒤집힌 전례가 있다(docs/QUANT_FINDINGS.md: alive/dead 가
76/97 → 16/3 로 창 변경 한 번에 뒤집힘). 그리고 계기판의 feature_alive_count /
feature_dead_count 는 Prometheus 경유 값이 n/a 로 나오던 공백이었다. 그래서
  (1) 판정 기준(임계값 전부)을 출력 JSON·MD 에 함께 남겨 재현·비교가 되게 하고,
  (2) 패널 직접 계산으로 그 공백을 메운다.
실측 2026-11-17: 현행 계기판 파일 data/reports/quant_scoreboard.json 에는
feature_alive_count/feature_dead_count 키가 **아예 없다**(스펙 작성 시점 "n/a"와 같은 방향 —
공백은 여전히 존재).

실측 근거 (조회 명령, 2026-11-17, 호스트 /usr/bin/python3):
  /usr/bin/python3 -c "import numpy as np; p=np.load('<panel>', allow_pickle=True);
      [print(k, p[k].shape, p[k].dtype) for k in p.files]"
    → X (54800, 213) float32 · feature_names (213,) <U33 · dates (54800,) object
      codes (54800,) object · price (54800,) float64
    → dates[:3] = ['2025-08-04' ...], dates[-3:] = ['2026-09-23' ...]
      unique codes = 200, unique dates = 279
    (npz 는 object 배열 포함이라 allow_pickle=True 가 필요 — False 면 ValueError)
  /usr/bin/python3 -c "..." (collections.Counter(zip(codes, dates)))
    → (code,date) 중복 0건, 코드별 행수 29~279(불균등), X NaN 1,398,300/11,672,400 ≈ 12%,
      전부 NaN 피처 0개, nonzero 가 하나라도 있는 피처 189/213 → 24개는 '전부 0-or-NaN'
  /usr/bin/python3 -c "import json; d=json.load(open('data/reports/r13_dead_feature_audit.json'));
      print(d['dead_total'])"  → 35 (buckets: buildable 11 / coverage_low 7 / market_level 7 /
      source_absent 10)

지표 정의는 기존 것을 재사용한다(스펙: "새 지표를 발명하지 않는다"):
  nnz_ratio_honest          = scripts/feature_coverage_report.py:143 그대로
                              ((notna)&(num!=0)).sum() / n  — 분모는 **전체 행**(NaN 제외 후
                              비영이 아니라, '결측·0 을 모두 값 없음으로 보는' 기존 정의).
  xsec_constant_ratio       = 같은 파일:156-160 그대로 — 날짜별 nunique ≤ 1 인 날짜 비율
                              (nunique 는 NaN 제외, 전부 NaN 인 날짜는 nunique=0 → 상수로 센다).
  (stock_unique_median 은 이 스크립트 판정에 쓰지 않음 — 스펙 3축엔 없음)

멱등 설계: 실행 시각을 출력에 넣지 않는다. 같은 패널 → 같은 JSON 바이트(시점은 panel.mtime 만).
산출물은 덮어쓰기라 재실행 안전(DELETE 후 적재와 동일 효과). MD 의 "N일 전"만 당일 기준이다.

자기신고(3분리): DB 접근 금지(스펙)라 scripts/dq_claim.py 의 record_claim(psycopg2 필요)을
**재사용할 수 없다**. 대신 출력 JSON 의 최상위 `claim` 키에 소스 수신(panel 행수/바이트) ·
파서 생성(통계 산출 피처 수) · 실제 저장(JSON 재독해로 대조한 피처 수)을 남긴다.

CLI:
  python3 scripts/feature_survival_report.py [--panel PATH] [--h 5] [--min-coverage 0.05]
          [--json-out PATH] [--md-out PATH] [--top-pairs 20] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

DEFAULT_PANEL = os.path.join(
    PROJ, "services", "xgboost-ml", "app", "models", "wf", "panel_prod200.npz")
JSON_DEFAULT = os.path.join(PROJ, "data", "reports", "feature_survival.json")
MD_DEFAULT = os.path.join(PROJ, "data", "reports", "feature_survival.md")
R13_PATH = os.path.join(PROJ, "data", "reports", "r13_dead_feature_audit.json")
REQUIRED_KEYS = ("X", "feature_names", "dates", "codes", "price")

# ── 임계값 (스펙 값 그대로 — 출력 params.thresholds 에 실려 재현됨) ──────────────────────
THR_NULL_RATIO = 0.999   # 스펙: DEAD_ALL_NULL = null_ratio ≥ 0.999
THR_XSEC_CONST = 0.95    # 스펙: CONST_MARKET = xsec_constant_ratio ≥ 0.95
THR_TIME_STD_EPS = 1e-9  # 스펙: CONST_STOCK = time_std ≈ 0. 종목 고정값의 일평균은 **정확히 0**
                         # (매 날짜 같은 값의 평균 → 같은 float)이므로, 부동소수 찌꺼기보다
                         # 두 자릿수 큰 1e-9 를 "≈0"으로 잡는다(판정에 실질 영향 없음).
THR_LEAK_L1 = 0.90       # 스펙: L1 |corr(feature_t, fwd_ret)| ≥ 0.90
THR_LEAK_L2 = 0.95       # 스펙: L2 |corr(feature_t, same_day_ret)| ≥ 0.95
LEAK_MIN_N = 500         # 스펙: 두 값이 모두 유효한 행 < 500 이면 판정 n/a
THR_REDUNDANT = 0.9999   # 스펙: |corr| ≥ 0.9999 중복 쌍 (참고용)

CLASS_ORDER = ["DEAD_ALL_NULL", "DEAD_CONST_ZERO", "DEAD_CONST_OTHER", "DEAD_SPARSE",
               "CONST_MARKET", "CONST_STOCK", "LEAK_SUSPECT", "ALIVE"]
DEAD_CLASSES = frozenset(CLASS_ORDER[:4])
CONDITIONAL_CLASSES = frozenset(CLASS_ORDER[4:7])
DEAD_TOP_N = 20          # MD 상위 죽은 피처 개수(스펙 §8)

CLAIM_NOTE = ("3분리: 소스 수신(panel 행수·바이트) / 파서 생성(통계 산출 피처 수) / "
              "실제 저장(JSON 재독해 대조 피처 수). DB 접근 금지(스펙)로 "
              "scripts/dq_claim.py record_claim 대신 출력 파일 안 자기신고.")


def _r6(x):
    """float → 소수 6자리 반올림(결정적). NaN/None → None(JSON null)."""
    if x is None:
        return None
    f = float(x)
    return None if math.isnan(f) else round(f, 6)


def _feature_stats(X, dates, codes):
    """피처별 통계(§2 전부 실측, NaN 은 값으로 세지 않는다). X 는 (rows, features) float64."""
    n_rows, n_feats = X.shape
    n_dates = len(set(dates))
    n_codes = len(set(codes))
    d_s = pd.Series(dates)
    c_s = pd.Series(codes)
    out = []
    for j in range(n_feats):
        col = pd.Series(X[:, j])
        valid = col.notna()
        n_valid = int(valid.sum())
        n_nonzero = int((valid & (col != 0)).sum())
        s = {
            "rows": n_rows,
            "null_ratio": _r6(col.isna().mean()),
            "nnz_ratio_honest": _r6(n_nonzero / n_rows),
            "std": _r6(col.std(ddof=0)),
            "nunique": int(col.nunique()),          # NaN 제외(pandas 기본)
            "coverage_days": _r6(valid.groupby(d_s).any().mean()),
            "coverage_codes": _r6(valid.groupby(c_s).any().mean()),
            "n_valid": n_valid,
            "n_nonzero": n_nonzero,
            "const_value": _r6(col[valid].iloc[0]) if n_valid else None,
        }
        g = col.groupby(d_s)
        d_std = g.std(ddof=0)                       # 날짜별 횡단면 표준편차(1값 → 0.0)
        s["xsec_std_median"] = _r6(d_std.median())
        # 기존 정의 그대로: 그 날짜 nunique ≤ 1 인 비율(전부 NaN 날짜는 nunique=0 → 포함).
        s["xsec_constant_ratio"] = _r6(g.nunique().le(1).mean())
        s["time_std"] = _r6(g.mean().std(ddof=0))   # 날짜별 평균의 시계열 표준편차
        out.append(s)
    return out, n_dates, n_codes


def _leak_checks(X, dates, codes, price, h):
    """누수 검사 2건(§4). 종목별 dates 오름차순 정렬 후 price 수익률.

    same_day_ret = price_t / price_{t-1} - 1 (당일 종가 수익률, 결정 시점에 알 수 없음)
    fwd_ret      = price_{t+h} / price_t - 1  (h번째 다음 행 기준 — 거래일 h일, 캘린더 아님;
                   코드별 결측 날짜는 '다음 행'이 h거래일 후가 아니게 만든다 → md 한계에 명시)
    표본 조건: 피처·fwd_ret·same_day_ret **세 값이 모두 유효한 행** 수가 LEAK_MIN_N 미만이면
    판정하지 않고 n/a(표본부족) 로 남긴다(스펙: "두 값이 모두 유효" — 양 검사에 공통 적용되는
    더 보수적인 단일 표본).
    """
    n_rows = X.shape[0]
    df = pd.DataFrame({"code": codes, "date": dates, "price": price})
    df["_i"] = np.arange(n_rows)
    df = (df.sort_values(["code", "date"])
            .drop_duplicates(subset=["code", "date"], keep="last")
            .reset_index(drop=True))
    same_day = df.groupby("code")["price"].pct_change(fill_method=None).to_numpy(dtype=np.float64)
    fwd = (df.groupby("code")["price"]
             .transform(lambda s: s.shift(-h) / s - 1.0)).to_numpy(dtype=np.float64)
    Xs = np.asarray(X[df["_i"].to_numpy()], dtype=np.float64)

    out = []
    for j in range(Xs.shape[1]):
        x = Xs[:, j]
        mask = ~np.isnan(x) & ~np.isnan(fwd) & ~np.isnan(same_day)
        n = int(mask.sum())
        rec = {"L1": None, "L2": None, "corr_L1": None, "corr_L2": None, "n": n}
        if n < LEAK_MIN_N:
            rec["note"] = "n/a(표본부족): n=%d < %d" % (n, LEAK_MIN_N)
        else:
            xs = x[mask]
            notes = []
            if xs.std() > 0:
                c1 = c2 = None
                ys = fwd[mask]
                if ys.std() > 0:
                    c1 = _r6(np.corrcoef(xs, ys)[0, 1])  # 반올림 후 판정 = 출력과 일치
                    rec["corr_L1"] = c1
                    rec["L1"] = bool(abs(c1) >= THR_LEAK_L1)
                else:
                    notes.append("L1 분산0")
                ys = same_day[mask]
                if ys.std() > 0:
                    c2 = _r6(np.corrcoef(xs, ys)[0, 1])
                    rec["corr_L2"] = c2
                    rec["L2"] = bool(abs(c2) >= THR_LEAK_L2)
                else:
                    notes.append("L2 분산0")
                s1 = "n/a" if c1 is None else "%.6f" % c1
                s2 = "n/a" if c2 is None else "%.6f" % c2
                if rec["L1"] or rec["L2"]:
                    note = "의심: L1=%s L2=%s (임계 L1>=%.2f L2>=%.2f)" % (
                        s1, s2, THR_LEAK_L1, THR_LEAK_L2)
                else:
                    note = "임계 미만: L1=%s L2=%s" % (s1, s2)
            else:
                note = "x 분산0 — 판정 불가"
            if notes:
                note += " | " + "; ".join(notes)
            rec["note"] = note
        out.append(rec)
    return out


def _redundant_pairs(X, names):
    """|corr| ≥ THR_REDUNDANT 인 피처 쌍(§5, 정보용 — 판정에 영향 없음).

    Pearson(pairwise-complete)을 마스크 행렬곱으로 **정확히** 계산한다:
      corr_ij = (n·Σxy − Σx·Σy) / √((n·Σx² − (Σx)²)(n·Σy² − (Σy)²))
    (Σ 는 둘 다 유효한 공통 행 위에서). numpy 1.26 에는 NaN 지원 corrcoef 가 없어
    pandas .corr()(213열에 수십 초) 대신 BLAS 로 푼다. 수치 안정성을 위해 각 열의
    **유효값 평균**을 먼저 빼고(열별 상수 이동은 Pearson 값에 영향 없음) 계산한다.
    ⚠ 2026-11-17 첫 버전은 열별 전역 mean/std 로 표준화 후 내적했는데, 이는 Pearson 과
    **다른** 값이다(공통 부분집합 mean/std 를 써야 |corr| ≤ 1). 실측: per_current ×
    sns_author_quality_score 에서 1.412266 이라는 불가능한 corr 이 나와 잡았다 — 수식 교체.
    """
    Xf = np.asarray(X, dtype=np.float64)
    mask = ~np.isnan(Xf)
    denom = mask.sum(axis=0)
    means = np.where(mask, Xf, 0.0).sum(axis=0) / np.maximum(denom, 1)
    Xc = np.where(mask, Xf - means[None, :], 0.0)          # 중심화(공통 행 Σ 에 기여 0)
    mf = mask.astype(np.float64)
    n_ij = mf.T @ mf                                       # 공통 유효 행 수
    sxy = Xc.T @ Xc                                        # Σ_공통 x'y'
    sx = Xc.T @ mf                                         # Σ_공통 x'
    sxx = (Xc * Xc).T @ mf                                 # Σ_공통 x'²
    num = n_ij * sxy - sx * sx.T
    a = n_ij * sxx - sx * sx                            # 열별 (n·Σx² − (Σx)²) — [i,j] 는 공통 행 기준
    with np.errstate(divide="ignore", invalid="ignore"):
        # 음수는 부동소수 찌꺼기(분산은 ≥ 0) → 0 으로 잘라 NaN 경고 없이
        den = np.sqrt(np.maximum(a * a.T, 0.0))
        corr = np.where(den > 0, num / den, np.nan)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if n_ij[i, j] >= 2 and not np.isnan(corr[i, j]) and abs(corr[i, j]) >= THR_REDUNDANT:
                pairs.append({"a": names[i], "b": names[j], "corr": _r6(corr[i, j])})
    pairs.sort(key=lambda p: (-abs(p["corr"]), p["a"], p["b"]))
    return pairs


def _duplicate_columns(X, names):
    """이름이 같은 컬럼 쌍(중복 컬럼) — 값까지 동일한지 함께 본다.

    WHY(2026-11-17 실측): 패널 213컬럼 중 고유 이름은 199개다. 이름이 겹치는 14쌍은
    값까지 완전히 같다(`np.allclose(X[:,16], X[:,196], equal_nan=True)` 등 14/14 True).
    학습에 같은 정보가 두 번 들어가면 피처 중요도가 쪼개지고 피처 수가 부풀려지므로
    생존 판정과 별개로 **결함으로 드러낸다**(패널 자체는 읽기 전용, 진단만).
    """
    Xf = np.asarray(X, dtype=np.float64)
    idx_by_name = {}
    for j, n in enumerate(names):
        idx_by_name.setdefault(n, []).append(j)
    out = []
    for n, idxs in idx_by_name.items():
        if len(idxs) < 2:
            continue
        same = True
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                if not np.allclose(Xf[:, idxs[a]], Xf[:, idxs[b]], equal_nan=True):
                    same = False
        out.append({"name": n, "columns": idxs, "identical": bool(same)})
    out.sort(key=lambda d: (-len(d["columns"]), d["name"]))
    return out


def _classify(s, lk, min_coverage):
    """판정 등급 — 우선순위대로 **한 개만**(§3). 사유 문자열도 남긴다(결정적)."""
    if s["null_ratio"] >= THR_NULL_RATIO:
        return "DEAD_ALL_NULL", ["null_ratio %.6f >= %.3f" % (s["null_ratio"], THR_NULL_RATIO)]
    if s["n_valid"] > 0 and s["n_nonzero"] == 0:
        return "DEAD_CONST_ZERO", ["유효값 %d개 전부 0" % s["n_valid"]]
    if s["nunique"] == 1:
        return "DEAD_CONST_OTHER", ["nunique == 1 (값 %.6f)" % s["const_value"]]
    reasons = []
    if s["coverage_days"] < min_coverage:
        reasons.append("coverage_days %.6f < %.6f" % (s["coverage_days"], min_coverage))
    if s["nnz_ratio_honest"] < min_coverage:
        reasons.append("nnz_ratio_honest %.6f < %.6f" % (s["nnz_ratio_honest"], min_coverage))
    if reasons:
        return "DEAD_SPARSE", reasons
    if s["xsec_constant_ratio"] >= THR_XSEC_CONST:
        return "CONST_MARKET", [
            "xsec_constant_ratio %.6f >= %.2f (시장 공통값)" % (s["xsec_constant_ratio"], THR_XSEC_CONST)]
    if s["time_std"] is not None and s["time_std"] < THR_TIME_STD_EPS:
        return "CONST_STOCK", ["time_std %.3e < %.1e (종목 고정값)" % (s["time_std"], THR_TIME_STD_EPS)]
    if lk["L1"] is True:
        reasons.append("L1 |corr|=%.6f >= %.2f (fwd_ret 와 동일선상)" % (lk["corr_L1"], THR_LEAK_L1))
    if lk["L2"] is True:
        reasons.append("L2 |corr|=%.6f >= %.2f (당일 수익률과 사실상 동일)" % (lk["corr_L2"], THR_LEAK_L2))
    if reasons:
        return "LEAK_SUSPECT", reasons
    return "ALIVE", []


def _r13_crosstab(r13_data, class_by_name, dead_names):
    """기존 r13 분류와의 교차표(§6). r13 파일은 읽기만 하고 수정하지 않는다."""
    feats = r13_data.get("features") or []
    bucket_of = {str(f.get("feature")): str(f.get("bucket") or "unknown") for f in feats
                 if f.get("feature")}
    r13_names = list(bucket_of)
    bxc = {}
    for nm in r13_names:
        cls = class_by_name.get(nm, "NOT_IN_PANEL")
        bxc.setdefault(bucket_of[nm], {}).setdefault(cls, 0)
        bxc[bucket_of[nm]][cls] += 1
    r13_set = set(r13_names)
    return {
        "r13_dead_total": int(r13_data.get("dead_total", len(feats))),
        "r13_ts": r13_data.get("ts"),
        "measured_dead": len(dead_names),
        "bucket_x_class": bxc,
        # 차이 나는 피처 이름(최대 20개씩)
        "r13_dead_not_now_dead": sorted(
            nm for nm in r13_names
            if class_by_name.get(nm, "NOT_IN_PANEL") not in DEAD_CLASSES)[:20],
        "now_dead_not_in_r13": sorted(nm for nm in dead_names if nm not in r13_set)[:20],
    }


def compute_survival(X, feature_names, dates, codes, price,
                     h=5, min_coverage=0.05, top_pairs=20, r13_data=None):
    """패널 배열 → 판정 payload(panel.path/mtime·claim 제외 — main 이 붙인다).

    실행 시각을 넣지 않으므로 같은 입력 → 같은 dict → 같은 JSON 바이트(멱등).
    """
    X = np.asarray(X, dtype=np.float64)
    dates = np.asarray(dates).astype(str)
    codes = np.asarray(codes).astype(str)
    price = np.asarray(price, dtype=np.float64)
    names = [str(n) for n in feature_names]

    stats, n_dates, n_codes = _feature_stats(X, dates, codes)
    leaks = _leak_checks(X, dates, codes, price, h)
    uniq_dates = sorted(set(dates.tolist()))

    features = []
    for j, nm in enumerate(names):
        cls, reasons = _classify(stats[j], leaks[j], min_coverage)
        features.append({"feature": nm, "class": cls, "reasons": reasons,
                         "stats": stats[j], "leak": leaks[j]})

    by_class = {c: 0 for c in CLASS_ORDER}
    class_by_name = {}
    for f in features:
        by_class[f["class"]] += 1
        class_by_name[f["feature"]] = f["class"]
    dead = sum(by_class[c] for c in DEAD_CLASSES)
    alive = by_class["ALIVE"]
    conditional = sum(by_class[c] for c in CONDITIONAL_CLASSES)
    # 계기판 정의(scripts/feature_coverage_report.py:222-223 '죽은 피처(=nonzero_ratio 0)',
    # filled=fillna(0) → nonzero = 결측 아닌 비영 행수)를 같은 패널에 적용한 수치 — 비교용.
    dash_dead = sum(1 for s in stats if s["n_nonzero"] == 0)
    dash_alive = len(stats) - dash_dead
    note = ("계기판 정의와 다름 — 계기판(feature_coverage.nonzero_ratio)은 결측 0 치환 후 "
            "nonzero>0 여부로 세므로 같은 패널에 그 정의를 적용하면 alive=%d dead=%d. "
            "이 파일은 DEAD_* 4종=dead(%d), ALIVE=alive(%d), "
            "CONST_MARKET/CONST_STOCK/LEAK_SUSPECT=조건부(%d, 횡단면·시계열 중 한 축만 유효)로 "
            "갈라 센다. 계기판 파일(quant_scoreboard.json)에는 feature_alive_count 키가 없어 "
            "(스펙 작성 시점 n/a) 이 파일이 그 공백을 메운다."
            % (dash_alive, dash_dead, dead, alive, conditional))

    payload = {
        "panel": {
            "shape": [int(X.shape[0]), int(X.shape[1])],
            "features": int(X.shape[1]),
            "dates": n_dates,
            "span": [uniq_dates[0], uniq_dates[-1]],
            "codes": n_codes,
        },
        "params": {
            "h": int(h),
            "min_coverage": float(min_coverage),
            "top_pairs": int(top_pairs),
            "thresholds": {
                "null_ratio": THR_NULL_RATIO,
                "xsec_const": THR_XSEC_CONST,
                "time_std_eps": THR_TIME_STD_EPS,
                "leak_L1": THR_LEAK_L1,
                "leak_L2": THR_LEAK_L2,
                "leak_min_n": LEAK_MIN_N,
                "redundant_corr": THR_REDUNDANT,
            },
        },
        "summary": {
            "total": len(features),
            "alive": alive,
            "dead": dead,
            "conditional": conditional,
            "by_class": by_class,
            "alive_and_dead": {"alive": alive, "dead": dead, "note": note},
        },
        "features": features,
        "redundant_pairs": _redundant_pairs(X, names)[:top_pairs],
        "duplicate_columns": _duplicate_columns(X, names),
        "r13_crosstab": (_r13_crosstab(r13_data, class_by_name, sorted(
            nm for nm, cls in class_by_name.items() if cls in DEAD_CLASSES))
            if r13_data is not None else None),
    }
    return payload


def _load_r13():
    """r13 분류 파일 읽기(없으면 None — 출력에 r13: null). 읽기만, 수정 금지."""
    if not os.path.exists(R13_PATH):
        return None
    try:
        with open(R13_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _render_md(payload):
    """사람용 요약(§8). 숫자는 전부 payload 계산값을 삽입 — 하드코딩 금지."""
    p, prm, sm, feats = payload["panel"], payload["params"], payload["summary"], payload["features"]
    t = prm["thresholds"]
    span0, span1 = p["span"]
    age = (date.today() - date.fromisoformat(span1)).days
    mtime_iso = datetime.fromtimestamp(p["mtime"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    L = []
    add = L.append
    add("# 피처 생존 판정 (3축: 분산 · 커버리지 · 누수)")
    add("")
    add("- 패널: `%s` — shape=(%d, %d), 기간 %s ~ %s (%d일), %d종목, mtime %s, "
        "스냅샷 최종일 기준 **%d일 전**"
        % (p["path"], p["shape"][0], p["shape"][1], span0, span1, p["dates"],
           p["codes"], mtime_iso, age))
    add("- 실행: `python3 scripts/feature_survival_report.py --h %d --min-coverage %g --top-pairs %d`"
        % (prm["h"], prm["min_coverage"], prm["top_pairs"]))
    add("- 판정: 총 %d → alive %d · dead %d · conditional %d"
        % (sm["total"], sm["alive"], sm["dead"], sm["conditional"]))
    add("")
    add("## 판정 우선순위 (먼저 걸리는 것 **하나만** 부여)")
    add("")
    add("1. `DEAD_ALL_NULL` 2. `DEAD_CONST_ZERO` 3. `DEAD_CONST_OTHER` 4. `DEAD_SPARSE` "
        "5. `CONST_MARKET` 6. `CONST_STOCK` 7. `LEAK_SUSPECT` 8. `ALIVE`")
    add("")
    add("## 클래스 분포")
    add("")
    add("| class | 개수 | 비율 |")
    add("|---|---|---|")
    for c in CLASS_ORDER:
        n = sm["by_class"][c]
        add("| %s | %d | %.1f%% |" % (c, n, 100.0 * n / sm["total"]))
    add("")

    dead_list = [f for f in feats if f["class"] in DEAD_CLASSES]
    dead_list.sort(key=lambda f: (CLASS_ORDER.index(f["class"]), f["feature"]))
    add("## 죽은 피처 상위 %d (DEAD_*)" % min(DEAD_TOP_N, len(dead_list)))
    add("")
    if dead_list:
        add("| # | feature | class | 사유 |")
        add("|---|---|---|---|")
        for i, f in enumerate(dead_list[:DEAD_TOP_N], 1):
            add("| %d | %s | %s | %s |" % (i, f["feature"], f["class"], "; ".join(f["reasons"])))
    else:
        add("- 0건")
    add("")

    leak_list = [f for f in feats if f["class"] == "LEAK_SUSPECT"]
    add("## LEAK_SUSPECT (전체 %d건)" % len(leak_list))
    add("")
    if leak_list:
        for f in leak_list:
            lk = f["leak"]
            add("- %s — L1=%s L2=%s n=%d (%s)"
                % (f["feature"], lk["L1"], lk["L2"], lk["n"], lk["note"]))
    else:
        add("- 0건")
    add("")
    if leak_list and all((f["leak"].get("L2") and not f["leak"].get("L1")) for f in leak_list):
        add("- 해석: 위 %d건은 전부 **L2(당일 수익률 동시상관)** 로만 걸렸고 L1(미래 h일 라벨 상관)은 0건이다. "
            "이들은 가격 파생 피처라 설계상 동시상관이 1에 가깝다 "
            "(`return_1d = close.pct_change()`, `price_vs_sector = stock_ret_1d − sector_momentum` — "
            "services/xgboost-ml/app/feature_engine/feature_pipeline.py:286,1057-1062). "
            "즉 **룩어헤드 증거가 아니라 정의상의 상관**이며, 확정 판정은 생성 코드 검토가 필요하다." % len(leak_list))
        add("")

    dups = payload.get("duplicate_columns") or []
    add("## 중복 컬럼 (이름이 같은 컬럼)")
    add("")
    if dups:
        ident = [d for d in dups if d["identical"]]
        add("- 고유 이름은 %d개 / 컬럼 %d개 — 이름이 겹치는 쌍 **%d개** (그중 값까지 동일 %d개)"
            % (payload["panel"]["features"] - sum(len(d["columns"]) - 1 for d in dups),
               payload["panel"]["features"], len(dups), len(ident)))
        add("- 대상: %s" % ", ".join("`%s`(col %s)" % (d["name"], d["columns"]) for d in dups))
        add("- 영향: 같은 정보가 두 번 들어가 피처 중요도가 쪼개지고 피처 수가 부풀려진다. "
            "이 리포트는 **진단만** 하고 패널은 고치지 않는다(읽기 전용).")
    else:
        add("- 0건")
    add("")

    r13 = payload.get("r13_crosstab")
    add("## r13 교차표")
    add("")
    if r13:
        add("- r13 `dead_total` %d vs 이번 실측 dead %d — 정의·창이 달라 차이가 난다."
            % (r13["r13_dead_total"], r13["measured_dead"]))
        add("- r13 에서 죽었지만 이번엔 죽지 않은 피처 (%d): %s"
            % (len(r13["r13_dead_not_now_dead"]),
               ", ".join(r13["r13_dead_not_now_dead"]) or "-"))
        add("- 이번엔 죽었지만 r13 에 없는 피처 (%d): %s"
            % (len(r13["now_dead_not_in_r13"]),
               ", ".join(r13["now_dead_not_in_r13"]) or "-"))
        add("")
        add("| r13 bucket | 이번 판정 class | 개수 |")
        add("|---|---|---|")
        for bucket in sorted(r13["bucket_x_class"]):
            for cls in sorted(r13["bucket_x_class"][bucket]):
                add("| %s | %s | %d |" % (bucket, cls, r13["bucket_x_class"][bucket][cls]))
    else:
        add("- `r13: null` — 파일 없음 (`%s`)" % os.path.relpath(R13_PATH, PROJ))
    add("")

    dash_dead = sum(1 for f in feats if f["stats"]["n_nonzero"] == 0)
    dash_alive = sm["total"] - dash_dead
    cl = payload["claim"]
    add("## 한계")
    add("")
    add("- 임계값(전부 `params.thresholds` 그대로 — 스펙 값, 결과에 맞춰 조정하지 않음): "
        "null_ratio>=%.3f · xsec_const>=%.2f · time_std<%.0e · L1>=%.2f · L2>=%.2f · "
        "누수 표본>=%d · 중복 corr>=%.4f · min_coverage=%.3f"
        % (t["null_ratio"], t["xsec_const"], t["time_std_eps"], t["leak_L1"], t["leak_L2"],
           t["leak_min_n"], t["redundant_corr"], prm["min_coverage"]))
    add("- 누수 검사(L1/L2)는 **의심 표시일 뿐 누수를 증명하지 않는다** — 생성 코드를 봐야 확정.")
    add("- 누수 표본은 피처·fwd_ret·당일 수익률 세 값이 모두 유효한 행이며, fwd 는 코드별 정렬 후 "
        "h번째 다음 행 기준(거래일 h일, 캘린더 아님 — 코드별 결측 날짜가 있으면 어긋날 수 있다).")
    add("- xsec_constant_ratio 는 기존 정의 그대로: 그 날짜 nunique<=1 인 비율(전부 NaN 날짜 포함).")
    add("- alive/dead 집계 정의: 이 파일은 DEAD_* 4종=dead(%d) · ALIVE=alive(%d) · 나머지 %d개는 "
        "조건부. 계기판 정의(결측 0 치환 nonzero>0)를 같은 패널에 적용하면 alive=%d dead=%d — "
        "세 숫자가 다를 수 있으므로 계기판 n/a 공백은 이 파일의 summary 로 메운다."
        % (sm["dead"], sm["alive"], sm["conditional"], dash_alive, dash_dead))
    add("- 패널 나이: 스냅샷 최종일로부터 %d일 경과 — 그 사이 시장 상황과 어긋날 수 있다." % age)
    add("- 자기신고: source_rows=%d · parsed_features=%d · persisted_features=%d (%s)"
        % (cl["source_rows"], cl["parsed_features"], cl["persisted_features"], cl["note"]))
    add("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="피처 생존 판정기 (스펙 docs/spec_feature_survival.md)")
    ap.add_argument("--panel", default=DEFAULT_PANEL)
    ap.add_argument("--h", type=int, default=5, help="fwd 수익률 수평선 (거래일)")
    ap.add_argument("--min-coverage", type=float, default=0.05,
                    help="DEAD_SPARSE 판정: coverage_days/nnz_ratio_honest 하한")
    ap.add_argument("--json-out", default=JSON_DEFAULT)
    ap.add_argument("--md-out", default=MD_DEFAULT)
    ap.add_argument("--top-pairs", type=int, default=20, help="redundant_pairs 최대 개수")
    ap.add_argument("--quiet", action="store_true", help="콘솔 출력 억제")
    args = ap.parse_args(argv)

    if not os.path.exists(args.panel):
        print("패널 없음: %s — 빈 산출물로 성공 위장하지 않음" % args.panel, file=sys.stderr)
        return 1
    try:
        with np.load(args.panel, allow_pickle=True) as p:
            missing = [k for k in REQUIRED_KEYS if k not in p.files]
            if missing:
                print("패널에 필수 키 없음: %s (실제: %s)" % (missing, sorted(p.files)),
                      file=sys.stderr)
                return 1
            X = p["X"]
            feature_names = p["feature_names"]
            dates = p["dates"]
            codes = p["codes"]
            price = p["price"]
    except Exception as e:  # noqa: BLE001 - 어떤 실패도 명확한 rc 로
        print("패널 읽기 실패(%s): %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    payload = compute_survival(X, feature_names, dates, codes, price,
                               h=args.h, min_coverage=args.min_coverage,
                               top_pairs=args.top_pairs, r13_data=_load_r13())
    payload["panel"]["path"] = args.panel
    payload["panel"]["mtime"] = os.path.getmtime(args.panel)

    os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.md_out) or ".", exist_ok=True)

    # 1차 저장(claim 없이) → 재독해로 실제 저장 피처 수 대조 → claim 포함 최종 저장.
    # claim 값도 결정적이므로 재실행 시 같은 바이트(멱등).
    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(json_text)
    try:
        reloaded = json.load(open(args.json_out, encoding="utf-8"))
        persisted = len(reloaded.get("features") or [])
    except (OSError, ValueError):
        persisted = -1
    payload["claim"] = {
        "runner": "feature_survival_report",
        "source": "panel_npz",
        "source_rows": int(np.asarray(X).shape[0]),
        "source_bytes": int(os.path.getsize(args.panel)),
        "parsed_features": int(np.asarray(X).shape[1]),
        "persisted_features": persisted,
        "persisted_files": [args.json_out, args.md_out],
        "note": CLAIM_NOTE,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(json_text)
    md_text = _render_md(payload)
    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(md_text)

    if not args.quiet:
        sm = payload["summary"]
        print("[feature_survival] %s shape=(%d,%d) %s~%s — alive=%d dead=%d conditional=%d (총 %d)"
              % (args.panel, payload["panel"]["shape"][0], payload["panel"]["shape"][1],
                 payload["panel"]["span"][0], payload["panel"]["span"][1],
                 sm["alive"], sm["dead"], sm["conditional"], sm["total"]))
        print("[feature_survival] json -> %s (%d bytes) | md -> %s (%d bytes)"
              % (args.json_out, os.path.getsize(args.json_out),
                 args.md_out, os.path.getsize(args.md_out)))
        print("[claim] %s -> panel_npz source=%d parsed=%d persisted=%d"
              % (payload["claim"]["runner"], payload["claim"]["source_rows"],
                 payload["claim"]["parsed_features"], payload["claim"]["persisted_features"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
