#!/usr/bin/env python3
"""rank_ic_money.py — 모델 점수의 **세션별 횡단면 랭크 IC**(=돈 방향 정렬) 검정기.

WHY (2026-10-05, 엔지니어 · CG111 후속)
  CG111 실측: 돈 지표 `top-k − 세션 풀평균` 의 검출 바닥은 무작위 랭킹 21회 기준
  sd k=3 0.415 · k=5 0.301 · k=10 0.159 (%p/세션) 다. 즉 k=3 단일 런 Δ 는 ±0.42 까지
  무작위로 나온다 → **CG96~CG112 의 모든 '노이즈' 판정은 '미검출'일 수 있다**(효과가
  검출 바닥보다 작았을 가능성). 세션 수를 늘리는 것(더 긴 패널)이 정공법이지만 그 전에
  **지표를 k=3 바스켓에서 전 종목 랭크상관(IC)으로 바꾸면 표본을 그대로 쓰고도 검정력을
  한 자릿수 이상 올릴 수 있다** — k=3 은 매 세션 3종목만 보지만 IC 는 매 세션 수백 종목을 본다.

  IC 는 매매 순기대가 아니다(정렬 통계다). 그러나 순기대가 양(+)이려면 **필요조건**이고,
  IC 의 t 가 0 과 구분되지 않으면 어떤 top-k 정책도 기대값을 만들 수 없다. 반대로 유의하면
  '랭킹에 돈 정보가 있다'가 되고, 그때는 소비 정책(절대문턱/top-k → 분위·IC 가중)이 다음 레버다.

정의
  IC_t = Spearman( y_pred , fwd_ret ) 그 날짜의 전 종목 (fwd_ret 결측 제외, n>=min_n)
  집계: mean IC, sd, t = mean/(sd/sqrt(n_sessions)), 양(+) 세션 비율, 앞/뒤 절반 평균.
  arm·control 을 주면 **같은 세션 짝** ΔIC 와 그 t 를 함께 낸다(프로토콜 차이 제거).

  implied_edge_pct = mean_IC × (세션별 fwd_ret 횡단면 sd 평균) × 1.755
    ← 정규 근사에서 상위 10% 의 평균 z = 1.755. 'IC 를 돈 단위로 옮긴 근사'이며 측정값이 아니다.

사전등록(이 스크립트의 기본 판정)
  신호있음 ⟺ mean_IC > 0 · t ≥ 2.0 · 양(+) 세션 ≥ 55% · 앞/뒤 절반 모두 양(+)
  그 밖은 '노이즈(미검출)' — '돈 정보 없음'으로 단정하지 않는다.

사용(컨테이너 — numpy/pandas 가 컨테이너에 있다)
  docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/rank_ic_money.py \
      --arm-jsonl /app/reports/overnight/cg108_at_preds.jsonl --arm-tag AT \
      --control-jsonl /app/reports/overnight/cg95_q30_all.jsonl --control-tag q30 \
      --json-out /app/reports/overnight/cg113_rank_ic.json'
  자체점검: python scripts/rank_ic_money.py --selftest
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict


# ── 순수 파이썬 랭크 (numpy 의존 없이 — 컨테이너/호스트 양쪽에서 돈다) ──────────
def _ranks(xs):
    """평균 순위(동점 처리). 1-based."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs, ys):
    return _pearson(_ranks(xs), _ranks(ys))


# ── 덤프 로딩 ───────────────────────────────────────────────────────────────
def load_rows(path, tag=None, fwd_cap=None):
    """dump jsonl → 행 리스트 [{date, code, y_pred, fwd_ret}] (+건너뛴 행 수).

    fwd_cap(%p) 를 주면 |fwd_ret| 이 그 이상인 행을 제외한다(상한가 매수 불가 근사).
    """
    rows, n_skip = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                n_skip += 1
                continue
            if tag is not None and r.get("exp") != tag:
                continue
            fr, yp, d = r.get("fwd_ret"), r.get("y_pred"), r.get("date")
            if fr is None or yp is None or d is None:
                n_skip += 1
                continue
            if fwd_cap is not None and abs(float(fr)) * 100.0 >= float(fwd_cap):
                n_skip += 1
                continue
            rows.append({"date": str(d), "code": str(r.get("code") or "").zfill(6),
                         "y_pred": float(yp), "fwd_ret": float(fr)})
    return rows, n_skip


def group_rows(rows):
    by_date = defaultdict(lambda: ([], []))
    for r in rows:
        by_date[r["date"]][0].append(r["y_pred"])
        by_date[r["date"]][1].append(r["fwd_ret"])
    return by_date


def load_dump(path, tag=None, fwd_cap=None):
    rows, n_skip = load_rows(path, tag, fwd_cap)
    return group_rows(rows), len(rows), n_skip


def load_market_series(codes, dmin, dmax, host=None, port=None):
    """market_data → {code: [(date_str, close, trading_value)]} (code·일자 정렬).

    체결성 필터용. 컨테이너에서 돌 때만 호출된다(psycopg2 필요).
    """
    import psycopg2                                    # noqa: WPS433 (지연 import)
    conn = psycopg2.connect(
        host=host or os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(port or os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = conn.cursor()
    cur.execute("SELECT stock_code, trade_date, close_price, trading_value "
                "FROM market_data WHERE stock_code = ANY(%s) "
                "AND trade_date BETWEEN %s AND %s ORDER BY stock_code, trade_date",
                (list(codes), dmin, dmax))
    out = defaultdict(list)
    for code, d, c, tv in cur.fetchall():
        out[str(code).zfill(6)].append(
            (str(d), float(c) if c is not None else None,
             float(tv) if tv is not None else None))
    cur.close()
    conn.close()
    return out


def fillable_filter(rows, series, max_day_chg=25.0, min_value=1e9, min_price=None,
                    exclude_limit_up=True, limit_up_pct=29.9):
    """체결 불가 행 제거 — `fillable_topk_expectancy.apply_filters` 와 같은 규칙.

    (당일등락 ≥ 상한가/--max-day-chg, 거래대금 < --min-value, 종가 < --min-price)
    """
    pos = {code: {d: i for i, (d, _c, _t) in enumerate(s)}
           for code, s in series.items()}
    keep, st = [], {"no_price": 0, "limit_up": 0, "day_chg": 0, "min_value": 0,
                    "min_price": 0}
    for r in rows:
        s = series.get(r["code"])
        i = pos.get(r["code"], {}).get(r["date"]) if s else None
        if s is None or i is None:
            st["no_price"] += 1
            continue
        close, tv = s[i][1], s[i][2]
        prev = s[i - 1][1] if i > 0 else None
        dc = ((close / prev - 1.0) * 100.0) if (close and prev) else None
        if exclude_limit_up and dc is not None and dc >= limit_up_pct:
            st["limit_up"] += 1
            continue
        if max_day_chg is not None and dc is not None and dc >= max_day_chg:
            st["day_chg"] += 1
            continue
        if min_value is not None and (tv is None or tv < min_value):
            st["min_value"] += 1
            continue
        if min_price is not None and (close is None or close < min_price):
            st["min_price"] += 1
            continue
        keep.append(r)
    return keep, len(rows) - len(keep), st


def decile_edge(by_date, n_dec=10):
    """점수 분위별 **당일 풀평균 대비 초과**(세션 등가중) + t — IC(전역 단조)와 top-k(꼬리)의
    불일치를 분해한다. 반환: {decile: {"edge_pct", "t", "n_sessions"}} (decile 1 = 최하위 점수).

    왜 필요한가(2026-10-05): 같은 행에서 '전역 랭크 IC 는 양(+)' 인데 'top-k 가 풀평균을
    하회'(CG98)한다는 관측은 단조 관계를 가정하면 모순이다 → 분위 프로파일로 어느 구간이
    기여하는지 직접 본다(꼬리 반전 vs 전 구간 단조). t 를 함께 내지 않으면 '이 수치가
    잡음인가'를 판단할 수 없다.
    """
    acc = defaultdict(list)
    for _d, (yp, fr) in by_date.items():
        if len(yp) < n_dec * 2:
            continue
        pool = statistics.mean(fr)
        order = sorted(range(len(yp)), key=lambda i: yp[i])
        n = len(order)
        for j, i in enumerate(order):
            dec = min(n_dec - 1, int(j * n_dec / n)) + 1
            acc[dec].append(fr[i] - pool)
    out = {}
    for k, v in sorted(acc.items()):
        if len(v) < 2:
            out[str(k)] = {"edge_pct": None, "t": None, "n_sessions": len(v)}
            continue
        m = statistics.mean(v)
        se = statistics.pstdev(v) / math.sqrt(len(v))
        out[str(k)] = {"edge_pct": m * 100.0, "t": (m / se) if se > 0 else None,
                       "n_sessions": len(v)}
    return out


def topk_vs_pool(by_date, ks=(3, 5, 10, 20, 50, 100)):
    """같은 행에서 top-k 바스켓 − 당일 풀평균 (%p/세션) — 돈 지표와 직접 대조용."""
    out = {}
    for k in ks:
        per = []
        for _d, (yp, fr) in by_date.items():
            if len(yp) < k + 2:
                continue
            idx = sorted(range(len(yp)), key=lambda i: -yp[i])[:k]
            per.append(statistics.mean(fr[i] for i in idx) - statistics.mean(fr))
        if len(per) < 2:
            continue
        m = statistics.mean(per) * 100.0
        sd = statistics.pstdev(per)
        se = sd / math.sqrt(len(per)) if sd > 0 else None
        out[str(k)] = {"delta_pct": m, "t": (m / 100.0 / se) if se else None,
                       "n_sessions": len(per)}
    return out


def ic_series(by_date, min_n=10):
    """date → IC (표본·분산 부족 날짜는 None)."""
    out = {}
    for d in sorted(by_date):
        yp, fr = by_date[d]
        if len(yp) < int(min_n):
            out[d] = None
            continue
        ic = spearman(yp, fr)
        out[d] = ic
    return out


def _agg(ics):
    vals = [v for v in ics.values() if v is not None]
    n = len(vals)
    if n < 2:
        return {"n_sessions": n, "mean_ic": None, "t": None}
    mean = statistics.mean(vals)
    sd = statistics.pstdev(vals) if n > 1 else 0.0
    se = sd / math.sqrt(n) if sd > 0 else None
    half = n // 2
    return {
        "n_sessions": n,
        "mean_ic": mean,
        "sd_ic": sd,
        "t": (mean / se) if se else None,
        "pos_session_share": sum(1 for v in vals if v > 0) / n,
        "first_half_ic": statistics.mean(vals[:half]) if half else None,
        "second_half_ic": statistics.mean(vals[half:]) if (n - half) else None,
    }


def _verdict(a, min_t=2.0, min_pos=0.55):
    if not a.get("mean_ic"):
        return "판정불가", "IC 표본 부족"
    ok = (a["mean_ic"] > 0 and (a.get("t") or 0) >= min_t
          and (a.get("pos_session_share") or 0) >= min_pos
          and (a.get("first_half_ic") or 0) > 0 and (a.get("second_half_ic") or 0) > 0)
    if ok:
        return "신호있음", ("랭킹에 돈 방향 정보가 있다: mean IC %.4f · t %.2f · 양세션 %.1f%% · "
                          "앞/뒤 %.4f/%.4f" % (a["mean_ic"], a["t"], a["pos_session_share"] * 100,
                                               a["first_half_ic"], a["second_half_ic"]))
    return "노이즈", ("미검출: mean IC %.4f · t %s · 양세션 %.1f%% · 앞/뒤 %s/%s"
                     % (a["mean_ic"], None if a.get("t") is None else round(a["t"], 2),
                        (a.get("pos_session_share") or 0) * 100,
                        None if a.get("first_half_ic") is None else round(a["first_half_ic"], 4),
                        None if a.get("second_half_ic") is None else round(a["second_half_ic"], 4)))


def _cs_sd(by_date):
    sds = [statistics.pstdev(fr) for _yp, fr in by_date.values() if len(fr) > 2]
    return statistics.mean(sds) if sds else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-jsonl")
    ap.add_argument("--arm-tag")
    ap.add_argument("--control-jsonl")
    ap.add_argument("--control-tag")
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--fwd-cap", type=float, default=None,
                    help="|fwd_ret| >= X%% 행 제외 (상한가 매수 불가 근사)")
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--fillable", action="store_true",
                    help="체결성 필터 적용(fillable_topk_expectancy 와 같은 규칙, DB 필요)")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--json-out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.arm_jsonl:
        ap.error("--arm-jsonl 필요 (또는 --selftest)")

    series_cache = {}

    def _load(path, tag):
        rows, n_skip = load_rows(path, tag, a.fwd_cap)
        fs = None
        if a.fillable and rows:
            key = (min(r["date"] for r in rows), max(r["date"] for r in rows))
            codes = sorted({r["code"] for r in rows})
            if key not in series_cache:
                import datetime                              # noqa: WPS433
                dmin = (datetime.date.fromisoformat(key[0])
                        - datetime.timedelta(days=10)).isoformat()   # prev_close 확보
                series_cache[key] = load_market_series(codes, dmin, key[1])
            keep, dropped, st = fillable_filter(
                rows, series_cache[key], a.max_day_chg, a.min_value, a.min_price)
            fs = {"dropped": dropped, "kept": len(keep), "reasons": st}
            rows = keep
        return group_rows(rows), len(rows), n_skip, fs

    by_date, n_rows, n_skip, fs = _load(a.arm_jsonl, a.arm_tag)
    ics = ic_series(by_date, a.min_n)
    agg = _agg(ics)
    cs_sd = _cs_sd(by_date)
    agg["implied_edge_pct"] = (agg["mean_ic"] * cs_sd * 1.755 * 100.0
                               if (agg.get("mean_ic") and cs_sd) else None)
    verdict, detail = _verdict(agg, a.min_t)

    out = {
        "arm": a.arm_tag or os.path.basename(a.arm_jsonl),
        "arm_jsonl": a.arm_jsonl,
        "fwd_cap_pct": a.fwd_cap,
        "fillable": bool(a.fillable),
        "fillable_filter": fs,
        "n_rows": n_rows, "n_rows_skipped": n_skip,
        "mean_cs_sd_ret": cs_sd,
        "ic": agg,
        "verdict": verdict,
        "detail": detail,
        "note": ("IC 는 순기대가 아니라 정렬 검정(필요조건). implied_edge_pct 는 정규 근사로 "
                 "상위 10% 스프레드를 환산한 근사치이며 측정값이 아니다."),
        "prereg": ("신호있음 ⟺ mean_IC>0 · t>=%.1f · 양(+)세션>=55%% · 앞/뒤 절반 모두 양(+)"
                   % a.min_t),
    }
    out["profile"] = {"decile_edge_pct": decile_edge(by_date),
                      "topk_vs_pool": topk_vs_pool(by_date)}
    if a.control_jsonl:
        cbd, cn_rows, cn_skip, cfs = _load(a.control_jsonl, a.control_tag)
        cics = ic_series(cbd, a.min_n)
        cagg = _agg(cics)
        cv, cd = _verdict(cagg, a.min_t)
        out["control"] = {
            "tag": a.control_tag or os.path.basename(a.control_jsonl),
            "n_rows": cn_rows, "ic": cagg, "verdict": cv, "detail": cd,
            "fillable_filter": cfs,
        }
        pairs = [(ics[d], cics[d]) for d in sorted(set(ics) & set(cics))
                 if ics[d] is not None and cics[d] is not None]
        if len(pairs) >= 2:
            diffs = [p[0] - p[1] for p in pairs]
            m = statistics.mean(diffs)
            sd = statistics.pstdev(diffs)
            se = sd / math.sqrt(len(diffs)) if sd > 0 else None
            out["paired"] = {
                "n_sessions": len(diffs), "mean_delta_ic": m,
                "t": (m / se) if se else None,
                "pos_session_share": sum(1 for x in diffs if x > 0) / len(diffs),
            }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.json_out:
        d = os.path.dirname(a.json_out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("[ok] %s" % a.json_out)
    return 0


# ── 자체점검 (합성 데이터 — 정답을 아는 IC 복원) ──────────────────────────────
def _selftest() -> int:
    fails = []
    n_ok = 0

    def check(name, cond, info=""):
        nonlocal n_ok
        if cond:
            n_ok += 1
            print("  [PASS] %s %s" % (name, info))
        else:
            fails.append(name)
            print("  [FAIL] %s %s" % (name, info))

    # 1) rank 함수: 동점 평균순위
    r = _ranks([10, 20, 20, 30])
    check("ranks_ties", r == [1.0, 2.5, 2.5, 4.0], str(r))

    # 2) 완전 단조 → IC = 1
    check("ic_monotone", abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12)

    # 3) 역단조 → IC = -1
    check("ic_antitone", abs(spearman([1, 2, 3, 4], [40, 30, 20, 10]) + 1.0) < 1e-12)

    # 4) 상수 → None (분산 0)
    check("ic_constant_none", spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None)

    # 5) 합성 세션 60개, 알려진 IC ≈ 0.20 (y = 0.2 z + 0.98 e, 결정적 난수)
    import random
    rnd = random.Random(7)
    by = {}
    for d in range(60):
        yp, fr = [], []
        for _ in range(120):
            z = rnd.gauss(0, 1)
            e = rnd.gauss(0, 1)
            yp.append(z)
            fr.append(0.20 * z + 0.98 * e)
        by["2026-%02d" % (d + 1)] = (yp, fr)
    ics = ic_series(by, min_n=10)
    agg = _agg(ics)
    check("synth_ic_recovered", abs(agg["mean_ic"] - 0.20) < 0.03,
          "mean_ic=%.4f" % agg["mean_ic"])
    check("synth_t_positive", (agg["t"] or 0) > 3.0, "t=%.2f" % (agg["t"] or 0))
    check("synth_verdict_signal", _verdict(agg)[0] == "신호있음", _verdict(agg)[0])

    # 6) 무관 세션 60개 → IC ≈ 0, 노이즈 판정
    by2 = {}
    for d in range(60):
        yp, fr = [], []
        for _ in range(120):
            yp.append(rnd.gauss(0, 1))
            fr.append(rnd.gauss(0, 1))
        by2["2026-%02d" % (d + 1)] = (yp, fr)
    agg2 = _agg(ic_series(by2, min_n=10))
    check("synth_null_near_zero", abs(agg2["mean_ic"]) < 0.03, "mean_ic=%.4f" % agg2["mean_ic"])
    check("synth_null_verdict_noise", _verdict(agg2)[0] == "노이즈", _verdict(agg2)[0])

    # 7) 표본 부족 날짜는 IC=None (n<min_n)
    small = {"d1": ([1.0, 2.0], [1.0, 2.0])}
    check("min_n_skip", ic_series(small, min_n=10)["d1"] is None)

    # 8) fwd_cap 필터가 행을 제거한다
    tmp = "/tmp/_rank_ic_cap.jsonl"
    with open(tmp, "w", encoding="utf-8") as f:
        for i in range(12):
            f.write(json.dumps({"date": "2026-01-01", "exp": "A", "y_pred": float(i),
                                "fwd_ret": 0.30 if i == 0 else float(i) / 1000.0}) + "\n")
    _, n1, _ = load_dump(tmp, "A", None)
    _, n2, _ = load_dump(tmp, "A", 29.9)
    check("fwd_cap_filters", n1 == 12 and n2 == 11, "%d→%d" % (n1, n2))
    os.remove(tmp)

    # 9) tag 필터
    tmp2 = "/tmp/_rank_ic_tag.jsonl"
    with open(tmp2, "w", encoding="utf-8") as f:
        for i in range(6):
            f.write(json.dumps({"date": "2026-01-01", "exp": "A" if i % 2 else "B",
                                "y_pred": float(i), "fwd_ret": 0.01 * i}) + "\n")
    _, n3, _ = load_dump(tmp2, "A", None)
    check("tag_filter", n3 == 3, str(n3))
    os.remove(tmp2)

    # 10) 짝 ΔIC: 같은 세션에서 arm 이 control 보다 나으면 양수
    print("[rank_ic_money selftest] %d PASS / %d FAIL" % (n_ok, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
