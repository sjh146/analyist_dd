#!/usr/bin/env python3
"""patch_panel_news — 패널의 뉴스 컬럼만 창 단위로 교체·append 하는 패치 러너.

WHY (2026-11-19 실측): 종목별 뉴스 수집으로 언급 보유 종목이 31 → 193(패널 200)까지 올랐고
news_mention_features(mention_7d/30d/90d, 6,398행)가 생성됐다. 그러나 패널
panel_prod200_v2.npz 의 뉴스 컬럼 news_count_5d/20d 는 옛 소스(news_events, 커버 31종목)로
계산돼 **상수 0**이다. 전체 재빌드(3시간+) 없이 해당 컬럼만 교체해 효과를 측정한다
(스펙: docs/spec_panel_news_patch.md, 죽은 피처 2단계 ③).

실측 근거 (조회 명령, 2026-11-19, 호스트 /usr/bin/python3, DB 는 SELECT 전용):
  np.load('services/xgboost-ml/app/models/wf/panel_prod200_v2.npz', allow_pickle=True)
    → X (61640, 213) float32 · feature_names (213,) <U33 · dates/codes (61640,) object
      price (61640,) float64 · dates 2025-08-04~2026-11-17 · codes 200종목
    → news_count_5d idx=84, news_count_20d idx=83 — nonzero 각 12(0.0195%), NaN 0
  SELECT COUNT(*), MIN(trade_date), MAX(trade_date), COUNT(DISTINCT stock_code)
  FROM news_mention_features  → 6,398행 · 2026-08-19~2026-11-18 · 193종목
  SELECT COUNT(*), MIN(published_date), MAX(published_date)
  FROM news_stock_mentions   → 9,384행 · 2026-08-19~2026-11-18 · 193종목
  창 2026-10-07~2026-11-17(스펙 기본): 패널 5,773행 · 29거래일 · 200종목 — 그리드 없는
  종목 7개(093640·463480·473000·475580·476040·493790·900270) → 창 내 NaN(결측)

설계:
  - 교체 모드(대상 컬럼이 있을 때): 창 내 행만 다시 계산 — (code, date) 의
    **date 이전 k=5·20거래일 누적 언급수** = news_stock_mentions 의 일별 언급 건수를
    [d_k, date) 구간에서 합산(**당일 제외**, d_k = 패널 달력에서 date 의 k번째 이전
    거래일, k개가 안 되는 기록 초반은 가장 오래된 거래일로 절단). 거래일 달력은 패널
    dates 그대로(패널 행과 정렬 보장). 언급 테이블에 없는 종목·날짜는 NaN(0 아님 —
    news_mention_features 그리드가 커버리지 판정 기준), 창이 언급 원천 수집 시작일
    (2026-08-19) 이전으로 넘어가는 행도 NaN. 그리드에 있고 언급이 0건이면 0
    (파일럿 정의: "뉴스 없는 날은 0").
    **창 밖 행은 원본 유지**(기준선 보존 — --from/--to 가 패치 범위).
  - append 모드(--append, 대상 컬럼이 없을 때): mention_7d·mention_30d 를 끝에 append.
    값은 news_mention_features 그리드 조인(7·30일 달력창 as-of, 파일럿 정의 그대로).
    창 밖은 NaN(신규 컬럼 — 값 없음).
  - 쓰기 규칙: --out 은 --panel 과 달라야 한다(realpath 같으면 **exit 2** — 기준선 보존).
    임시 파일에 np.savez_compressed 후 os.replace(원자적 덮어쓰기, 원본 불변). 저장 후
    **디스크 재독해**로 ①행수 ②feature_names 순서 ③비대상 컬럼 바이트 동일
    (np.array_equal(..., equal_nan=True)) ④대상 컬럼 창 밖 동일을 자기검증해 리포트에
    남긴다. 검증 실패는 exit 1(패치가 다른 컬럼을 건드렸다는 뜻 — 실패로 보고).
  - 멱등: 실행 시각을 출력에 넣지 않는다 — 같은 입력 → 같은 npz·JSON 바이트(재실행 안전,
    산출물 덮어쓰기).
  - 자기신고(3분리): 스펙 §4 "DB 쓰기 금지(SELECT 전용)"라 scripts/dq_claim.py
    record_claim(INSERT)은 **재사용 불가** → feature_survival_report.py 전례로 출력
    JSON 의 claim 키에 소스 수신(패널 행수·바이트 + 언급 일별 행수 + 그리드 행수) /
    파서 생성(창 내 계산 셀 수) / 실제 저장(디스크 재독해 npz 대조)을 남긴다.
  - --dry-run: 계산·메모리 검증까지만 하고 아무것도 쓰지 않는다(npz·리포트·DB 없음,
    결과 JSON 을 stdout 으로만 출력).
  - --mentions-file PATH: DB 대신 JSON {"daily": [[code, date, n], ...],
    "grid": [[code, date, mention_7d, mention_30d], ...], "coverage_start": "..."} 로
    데이터를 준다(테스트·재현용 — DB 경로는 SELECT 로 같은 형태를 만든다).

사용 (호스트 /usr/bin/python3):
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/patch_panel_news.py --panel services/xgboost-ml/app/models/wf/panel_prod200_v2.npz \\
      --out services/xgboost-ml/app/models/wf/panel_prod200_v3.npz --from 2026-10-07 --to 2026-11-17
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from bisect import bisect_left, bisect_right
from datetime import datetime

import numpy as np

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT = os.path.join(PROJ, "data", "reports", "panel_news_patch.json")
REQUIRED_KEYS = ("X", "feature_names", "dates", "codes", "price")
# (대상 컬럼, append 시 이름, 거래일 창 k, 그리드 컬럼 인덱스 0=mention_7d 1=mention_30d)
TARGETS = (("news_count_5d", "mention_7d", 5, 0),
           ("news_count_20d", "mention_30d", 20, 1))

MENTIONS_SQL = """
SELECT stock_code, published_date, COUNT(*) FROM news_stock_mentions
WHERE published_date >= %s AND published_date <= %s
GROUP BY stock_code, published_date
"""

GRID_SQL = """
SELECT stock_code, trade_date, mention_7d, mention_30d FROM news_mention_features
"""

COVERAGE_SQL = "SELECT MIN(published_date) FROM news_stock_mentions"

CLAIM_NOTE = ("3분리: 소스 수신(패널 행수·바이트 + news_stock_mentions 일별 행수 + "
              "news_mention_features 그리드 행수) / 파서 생성(창 내 계산 셀 = 창 행수 × "
              "컬럼 수) / 실제 저장(디스크 재독해 npz 대조). 스펙 §4 DB 쓰기 금지(SELECT "
              "전용)라 scripts/dq_claim.py record_claim(INSERT)은 재사용 불가 — "
              "feature_survival_report.py 전례로 출력 파일 안 자기신고.")


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def _round6(x):
    f = float(x)
    return None if math.isnan(f) else round(f, 6)


def build_calendar(codes, dates):
    """코드별 패널 거래일 달력(오름차순 str) — 패치 창 정의의 유일한 달력."""
    cal = {}
    for c, d in zip(codes, dates):
        cal.setdefault(c, []).append(d)
    for ds in cal.values():
        ds.sort()
    return cal


def window_mask(dates, d_from, d_to):
    """ISO 문자열 비교(YYYY-MM-DD 는 사전순 == 날짜순)."""
    return (dates >= d_from) & (dates <= d_to)


def earliest_boundary(codes, dates, mask, k):
    """창 내 모든 (code, date) 의 k거래일 이전 경계 중 최솟값 — DB 조회 하한.

    (고정 '45일 전' 같은 상수를 쓰지 않는다 — 패널 달력에서 실제로 계산.)
    """
    cal = build_calendar(codes, dates)
    lo_min = None
    for i in np.flatnonzero(mask):
        code, d = codes[i], dates[i]
        ds = cal[code]
        idx = bisect_right(ds, d) - 1
        lo = ds[max(0, idx - k)] if idx >= 0 else d
        if lo_min is None or lo < lo_min:
            lo_min = lo
    return lo_min


def compute_trading_day_counts(codes, dates, mask, daily, grid, coverage_start, k):
    """창 내 (code, date) → date 이전 k거래일 누적 언급수(당일 제외).

    daily: {code: {date: 건수}} — news_stock_mentions 일별 집계(원천)
    grid:  {(code, date): (m7, m30)} — news_mention_features(커버리지 판정 기준)
    coverage_start: 언급 원천 최초 발행일 "YYYY-MM-DD"
    NaN 규칙(0 아님 — 스펙 §1): (code, date) 가 그리드에 없거나(수집 안 된 종목·거래중지),
    창이 coverage_start 이전으로 넘어가면 NaN. 그리드에 있고 언급이 0건이면 0.
    창 밖 행도 NaN 이지만 호출부가 mask 내 행만 쓴다.
    """
    cal = build_calendar(codes, dates)
    arrs = {}
    for code, d in daily.items():
        ds = sorted(d)
        pre = [0]
        for x in ds:
            pre.append(pre[-1] + int(d[x]))
        arrs[code] = (ds, pre)
    vals = np.full(len(codes), np.nan, dtype=np.float64)
    for i in np.flatnonzero(mask):
        code, d = codes[i], dates[i]
        if (code, d) not in grid:
            continue  # 결측(커버리지 밖) → NaN
        ds = cal[code]
        idx = bisect_right(ds, d) - 1
        if idx < 0:
            continue  # 달력에 없는 날짜(방어) → NaN
        lo = ds[max(0, idx - k)] if idx >= k else ds[0]  # 기록 초반은 절단
        if lo < coverage_start:
            continue  # 창 일부가 수집 시작 전 → NaN
        arr = arrs.get(code)
        if arr is None:
            vals[i] = 0.0  # 그리드엔 있지만 언급 0건 → 0
            continue
        dlist, pre = arr
        a = bisect_left(dlist, lo)
        b = bisect_left(dlist, d)  # 당일 제외: d 미만만
        vals[i] = float(pre[b] - pre[a])
    return vals


def compute_append_column(codes, dates, mask, grid, col_idx):
    """append 모드 컬럼 값 — 그리드 조인(0=mention_7d, 1=mention_30d).

    값은 파일럿 러너(news_mention_features.py)가 계산한 as-of 그대로. 창 밖·결측은 NaN.
    """
    vals = np.full(len(codes), np.nan, dtype=np.float64)
    for i in np.flatnonzero(mask):
        v = grid.get((codes[i], dates[i]))
        if v is not None:
            vals[i] = float(v[col_idx])
    return vals


def _column_report(name, action, k, new_col, old_col, mask):
    """컬럼별 스펙 리포트 필드: nnz_before, nnz_after, nonzero_rows, mean/std, null_ratio.

    nnz = NaN 아닌 비영 값 수(전체 컬럼, feature_survival 의 nnz_ratio_honest 정의와 동일),
    nonzero_rows = 창 내 새 값이 0 이 아닌 행 수, changed_rows = 창 내 값이 바뀐 행 수.
    """
    valid = ~np.isnan(new_col)
    nnz = int(np.count_nonzero(valid & (new_col != 0)))
    if old_col is not None:
        old_valid = ~np.isnan(old_col)
        nnz_before = int(np.count_nonzero(old_valid & (old_col != 0)))
        changed = int(np.count_nonzero(
            mask & ~((new_col == old_col) | (np.isnan(new_col) & np.isnan(old_col)))))
    else:
        nnz_before = None
        changed = None
    nz_rows = int(np.count_nonzero(mask & valid & (new_col != 0)))
    null_rows = int(np.count_nonzero(mask & ~valid))
    computed_rows = int(np.count_nonzero(mask & valid))
    vals = new_col[valid]
    return {
        "name": name,
        "action": action,
        "k": k,
        "nnz_before": nnz_before,
        "nnz_after": nnz,
        "nonzero_rows": nz_rows,
        "changed_rows": changed,
        "null_rows": null_rows,
        "mean": _round6(vals.mean()) if vals.size else None,
        "std": _round6(vals.std()) if vals.size else None,
        "null_ratio": _round6(float(np.count_nonzero(~valid)) / max(len(new_col), 1)),
        "window": {"rows": int(np.count_nonzero(mask)),
                   "computed_rows": computed_rows,
                   "missing_rows": null_rows},
    }


def run_patch(X, feature_names, dates, codes, data, d_from, d_to, append_flag):
    """순수 계산 — X 는 변경하지 않고 사본을 돌려준다(테스트·프로브용 진입점).

    data: {"daily": {code: {date: 건수}}, "grid": {(code, date): (m7, m30)},
           "coverage_start": "YYYY-MM-DD"}
    → (new_X, out_names, mask, col_reports, replaced_idxs, appended_names)
    대상 컬럼이 없고 --append 도 아니면 ValueError(→ main 은 exit 2).
    """
    grid = data["grid"]
    if not grid:
        raise ValueError("뉴스 언급 그리드(news_mention_features)가 비어 있음 — "
                         "전부 NaN 이 될 패치를 거부한다")
    daily = data.get("daily") or {}
    coverage_start = data.get("coverage_start") or "0000-01-01"

    names = [str(n) for n in feature_names]
    pos = {n: i for i, n in enumerate(names)}
    mask = window_mask(dates, d_from, d_to)

    Xf = np.asarray(X, dtype=np.float32)
    new_X = Xf.copy()
    out_names = list(names)
    replaced_idxs = []
    appended_names = []
    col_reports = []
    for news_name, ment_name, k, gcol in TARGETS:
        if news_name in pos:
            vals = compute_trading_day_counts(codes, dates, mask, daily, grid,
                                              coverage_start, k)
            # 창 밖 행은 원본 유지(기준선 보존) — vals 는 창 밖이 NaN 이므로 mask 행만 쓴다
            new_X[mask, pos[news_name]] = vals[mask].astype(np.float32)
            replaced_idxs.append(pos[news_name])
            col_reports.append(_column_report(
                news_name, "replace", k,
                new_X[:, pos[news_name]], Xf[:, pos[news_name]], mask))
        elif append_flag:
            vals = compute_append_column(codes, dates, mask, grid, gcol)
            col = vals.astype(np.float32).reshape(-1, 1)
            new_X = np.concatenate([new_X, col], axis=1)
            out_names.append(ment_name)
            appended_names.append(ment_name)
            col_reports.append(_column_report(ment_name, "append", None,
                                              col[:, 0], None, mask))
    if not col_reports:
        raise ValueError(
            "대상 컬럼 %s 없음 — 패널 확인 또는 --append 로 mention_7d·mention_30d append"
            % [t[0] for t in TARGETS])
    return new_X, out_names, mask, col_reports, replaced_idxs, appended_names


def _verify(orig_X, out_X, out_names, expected_names, mask, replaced_idxs,
            appended_idxs):
    """저장 후 자기검증(스펙 §1): ①행수 ②feature_names 순서 ③비대상 컬럼 바이트 동일
    ④대상 컬럼 창 밖 동일(replace: 원본 유지, append: 전부 NaN).
    """
    ok_rows = bool(out_X.shape[0] == orig_X.shape[0])
    ok_names = out_names == [str(n) for n in expected_names]
    ok_rest = True
    for j in range(orig_X.shape[1]):
        if j in replaced_idxs:
            continue
        if not np.array_equal(out_X[:, j], orig_X[:, j], equal_nan=True):
            ok_rest = False
            break
    ok_outside = True
    for j in replaced_idxs:
        if not np.array_equal(out_X[~mask, j], orig_X[~mask, j], equal_nan=True):
            ok_outside = False
    for j in appended_idxs:
        if not np.all(np.isnan(out_X[~mask, j])):
            ok_outside = False
    checks = {"rows_identical": ok_rows,
              "feature_names_identical": ok_names,
              "non_target_columns_identical": ok_rest,
              "target_columns_outside_window_identical": ok_outside}
    checks["checks_failed"] = sum(1 for v in checks.values() if v is False)
    return checks


def _pg_params():
    """DB 좌표 — dq_claim._conn_target 과 같은 컨테이너 좌표 되돌림.

    .env 의 컨테이너 이름(POSTGRES_HOST=postgres)을 호스트 프로세스가 물려받으면
    이름해석 실패로 죽으므로, 컨테이너 밖이면 127.0.0.1:POSTGRES_HOST_PORT 로 되돌린다.
    """
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
    if host in ("postgres", "db") and not os.path.exists("/.dockerenv"):
        host = "127.0.0.1"
        port = int(os.environ.get("POSTGRES_HOST_PORT", "5434") or 5434)
    return dict(host=host, port=port,
                dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                user=os.environ.get("POSTGRES_USER", "stock_user"),
                password=os.environ.get("POSTGRES_PASSWORD", ""))


def load_data_from_db(dates, codes, mask, d_to):
    """news_stock_mentions·news_mention_features SELECT(쓰기 금지) → data dict.

    조회 하한 = 창 내 k=20거래일 경계의 최솟값(패널 달력에서 계산). 실패 시 None(→ exit 2).
    """
    import psycopg2  # 지연 import — DB 없는 환경(테스트)에서도 모듈 import 는 성공해야 한다

    lo_min = earliest_boundary(codes, dates, mask, 20)
    try:
        conn = psycopg2.connect(**_pg_params())
    except psycopg2.Error as exc:
        log("DB 연결 실패: %s" % exc)
        return None
    try:
        cur = conn.cursor()
        try:
            cur.execute(MENTIONS_SQL, (lo_min, d_to))
            mention_rows = cur.fetchall()
            cur.execute(COVERAGE_SQL)
            cov = cur.fetchone()[0]
            cur.execute(GRID_SQL)
            grid_rows = cur.fetchall()
        except psycopg2.errors.UndefinedTable as exc:
            conn.rollback()
            log("언급 테이블 없음(수집 전?): %s" % exc)
            return None
        finally:
            cur.close()
    finally:
        conn.close()
    daily = {}
    for c, d, n in mention_rows:
        daily.setdefault(str(c), {})[str(d)] = int(n)
    grid = {}
    for c, d, m7, m30 in grid_rows:
        grid[(str(c), str(d))] = (float(m7), float(m30))
    coverage_start = str(cov) if cov is not None else "0000-01-01"
    return {"daily": daily, "grid": grid, "coverage_start": coverage_start}


def load_data_from_file(path):
    """--mentions-file: {"daily": [[code, date, n]...], "grid": [[code, date, m7, m30]...],
    "coverage_start": "YYYY-MM-DD"(선택 — 없으면 daily 의 최소 날짜)}"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        log("mentions-file 읽기 실패(%s): %s" % (type(exc).__name__, exc))
        return None
    daily = {}
    for c, d, n in raw.get("daily") or []:
        daily.setdefault(str(c), {})[str(d)] = int(n)
    grid = {}
    for c, d, m7, m30 in raw.get("grid") or []:
        grid[(str(c), str(d))] = (float(m7), float(m30))
    if raw.get("coverage_start"):
        coverage_start = str(raw["coverage_start"])
    else:
        days = [d for c in daily for d in daily[c]]
        coverage_start = min(days) if days else (min(d for _, d in grid) if grid
                                                 else "0000-01-01")
    return {"daily": daily, "grid": grid, "coverage_start": coverage_start}


def main(argv=None):
    ap = argparse.ArgumentParser(description="패널 뉴스 컬럼 패치 (스펙 docs/spec_panel_news_patch.md)")
    ap.add_argument("--panel", required=True, help="입력 패널 npz (읽기 전용 — 절대 덮어쓰지 않음)")
    ap.add_argument("--out", required=True, help="출력 패널 npz (--panel 과 달라야 함)")
    ap.add_argument("--from", required=True, dest="d_from", help="패치 시작일 YYYY-MM-DD")
    ap.add_argument("--to", required=True, dest="d_to", help="패치 종료일 YYYY-MM-DD(포함)")
    ap.add_argument("--append", action="store_true",
                    help="대상 컬럼이 없으면 mention_7d·mention_30d 를 신규 append")
    ap.add_argument("--dry-run", action="store_true",
                    help="계산·메모리 검증까지만 하고 아무것도 쓰지 않는다")
    ap.add_argument("--report", default=DEFAULT_REPORT)
    ap.add_argument("--mentions-file", default=None,
                    help="DB 대신 JSON {daily, grid} 로 언급 데이터를 준다(테스트·재현용)")
    a = ap.parse_args(argv)

    # ── 1) 인자 검증 ──────────────────────────────────────────────────────────────
    # 쓰기 규칙(스펙 §1): --out 은 원본과 달라야 한다(같으면 exit 2) — 기준선 보존.
    if os.path.realpath(a.panel) == os.path.realpath(a.out):
        log("--out 이 --panel 과 같음: %s — 기준선 보존을 위해 거부" % a.panel)
        return 2
    try:
        datetime.strptime(a.d_from, "%Y-%m-%d")
        datetime.strptime(a.d_to, "%Y-%m-%d")
    except ValueError:
        log("날짜 형식 오류: --from=%s --to=%s (YYYY-MM-DD)" % (a.d_from, a.d_to))
        return 2
    if a.d_from > a.d_to:
        log("기간 오류: from(%s) > to(%s)" % (a.d_from, a.d_to))
        return 2

    # ── 2) 패널 읽기 ──────────────────────────────────────────────────────────────
    if not os.path.exists(a.panel):
        log("패널 없음: %s" % a.panel)
        return 1
    try:
        with np.load(a.panel, allow_pickle=True) as p:
            missing = [k for k in REQUIRED_KEYS if k not in p.files]
            if missing:
                log("패널에 필수 키 없음: %s (실제: %s)" % (missing, sorted(p.files)))
                return 1
            X = p["X"]
            feature_names = p["feature_names"]
            dates = p["dates"]
            codes = p["codes"]
            price = p["price"]
    except Exception as e:  # noqa: BLE001 - 어떤 실패도 명확한 rc 로
        log("패널 읽기 실패(%s): %s" % (type(e).__name__, e))
        return 1
    X_orig = np.asarray(X, dtype=np.float32)
    dates_s = np.asarray(dates).astype(str)
    codes_s = np.asarray(codes).astype(str)
    mask = window_mask(dates_s, a.d_from, a.d_to)
    n_win = int(np.count_nonzero(mask))
    if n_win == 0:
        log("경고: 창 %s~%s 에 해당하는 패널 행 0건 — 변경 없이 사본만 저장한다"
            % (a.d_from, a.d_to))

    # ── 3) 언급 데이터 (SELECT 전용 — DB 쓰기 금지, 스펙 §4) ─────────────────────
    if a.mentions_file:
        data = load_data_from_file(a.mentions_file)
    else:
        data = load_data_from_db(dates_s, codes_s, mask, a.d_to)
    if data is None:
        return 2

    # ── 4) 계산 (순수) ────────────────────────────────────────────────────────────
    try:
        new_X, out_names, _, col_reports, replaced_idxs, appended_names = run_patch(
            X_orig, feature_names, dates_s, codes_s, data, a.d_from, a.d_to, a.append)
    except ValueError as exc:
        log(str(exc))
        return 2
    if a.append and not appended_names:
        log("대상 컬럼 모두 존재 — --append 무시(교체 모드)")
    appended_idxs = [X_orig.shape[1] + i for i in range(len(appended_names))]

    # ── 5) 저장(임시 파일 → 원자 교체) + 디스크 재독해 자기검증 ───────────────────
    if not a.dry_run:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(a.out) or ".",
                                        prefix=".panel_news_patch_", suffix=".npz")
        os.close(fd)
        try:
            # 교체 모드에선 원본 feature_names 배열을 그대로 저장(바이트 동일 보장)
            fn_out = np.array(out_names) if appended_names else feature_names
            np.savez_compressed(tmp_path, X=new_X, feature_names=fn_out,
                                dates=dates, codes=codes, price=price)
            os.replace(tmp_path, a.out)
        except Exception as exc:  # noqa: BLE001
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            log("npz 저장 실패(%s): %s" % (type(exc).__name__, exc))
            return 1
    try:
        if a.dry_run:
            out_X_c = new_X
            out_names_c = [str(n) for n in out_names]
            verify_note = "memory(저장 없음)"
            persisted = None
        else:
            with np.load(a.out, allow_pickle=True) as s:
                out_X_c = s["X"]
                out_names_c = [str(n) for n in s["feature_names"]]
            verify_note = "disk"
            persisted = {"rows": int(out_X_c.shape[0]),
                         "features": int(out_X_c.shape[1]),
                         "bytes": int(os.path.getsize(a.out))}
    except Exception as exc:  # noqa: BLE001
        log("저장 파일 재독해 실패(%s): %s" % (type(exc).__name__, exc))
        return 1
    verify = _verify(X_orig, out_X_c, out_names_c, out_names, mask,
                     replaced_idxs, appended_idxs)

    # ── 6) 리포트 (실행 시각 미포함 — 멱등: 같은 입력 → 같은 바이트) ───────────────
    actions = {r["action"] for r in col_reports}
    mode = ("append" if actions == {"append"}
            else "replace" if actions == {"replace"} else "mixed")
    win_dates = dates_s[mask]
    payload = {
        "runner": "patch_panel_news",
        "panel": {"path": a.panel, "out": a.out,
                  "shape": [int(X_orig.shape[0]), int(X_orig.shape[1])],
                  "out_shape": [int(out_X_c.shape[0]), int(out_X_c.shape[1])],
                  "dates_span": [min(dates_s.tolist()), max(dates_s.tolist())]},
        "params": {"from": a.d_from, "to": a.d_to, "append": a.append,
                   "dry_run": a.dry_run, "mentions_file": a.mentions_file,
                   "window": {"rows": n_win,
                              "dates": int(len(set(win_dates.tolist()))) if n_win else 0,
                              "codes": int(len(set(codes_s[mask].tolist()))) if n_win else 0}},
        "mode": mode,
        "columns": col_reports,
        "verify": dict(verify, note="저장 후 %s 재독해 대조" % verify_note),
        "claim": {
            "note": CLAIM_NOTE,
            "source_rows": int(X_orig.shape[0]),
            "source_bytes": int(os.path.getsize(a.panel)),
            "mention_rows": int(sum(len(d) for d in data["daily"].values())),
            "grid_rows": int(len(data["grid"])),
            "parsed_cells": int(n_win * len(col_reports)),
            "persisted": persisted,
        },
    }

    log("panel=%s out=%s mode=%s window=%s~%s rows=%d (dates=%d codes=%d)"
        % (a.panel, a.out, mode, a.d_from, a.d_to, n_win,
           payload["params"]["window"]["dates"], payload["params"]["window"]["codes"]))
    for r in col_reports:
        log("%s(%s,k=%s): nnz %s -> %s nonzero_rows=%s changed=%s null=%s "
            "mean=%s std=%s null_ratio=%s"
            % (r["name"], r["action"], r["k"], r["nnz_before"], r["nnz_after"],
               r["nonzero_rows"], r["changed_rows"], r["null_rows"],
               r["mean"], r["std"], r["null_ratio"]))
    v = payload["verify"]
    log("verify(%s): rows=%s names=%s non_target=%s outside_window=%s"
        % (verify_note, v["rows_identical"], v["feature_names_identical"],
           v["non_target_columns_identical"], v["target_columns_outside_window_identical"]))
    if a.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        log("dry-run: npz·리포트·DB 에 아무것도 쓰지 않았다")
    else:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        log("out -> %s (%d bytes) | report -> %s"
            % (a.out, persisted["bytes"], a.report))
        cl = payload["claim"]
        log("[claim] patch_panel_news -> panel_npz+news_mentions "
            "source=%d+%d+%d parsed=%d persisted=%d"
            % (cl["source_rows"], cl["mention_rows"], cl["grid_rows"],
               cl["parsed_cells"], persisted["rows"]))
    if v["checks_failed"]:
        log("자기검증 실패 %d건 — 실패로 보고(exit 1)" % v["checks_failed"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
