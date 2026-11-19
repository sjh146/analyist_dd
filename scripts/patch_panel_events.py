#!/usr/bin/env python3
"""patch_panel_events — event_rolling_features 8개를 패널 뒤에 **append**(기존 컬럼 무수정).

⚠ 이 파일은 **같은 경로의 옛 도구(2026-09-25)를 대체**한다(스펙 docs/spec_event_rolling.md §2가
이 경로를 신규 도구 위치로 명시). 옛 도구는 event_*_5d 18개를 DB에서 **같은날 값**으로
append 하고 결측을 **0으로 채우던** 일회성 부활 실험용이었다(컨테이너 전용 --in/--out,
`add = np.full(..., args.fill)` 로 0 채움). 이번 스펙이 금지하는 두 가지(당일 조회·0 채움)를
그대로 하고 있었으므로 대체 대상이 맞다. 옛 코드는 git 히스토리에 남고, 참조처는 일회성
스크립트(scripts/_cg29_*.py, _cg67_68_register.py, wf_label_sweep.py)뿐이다.

WHY (스펙 §2): event_*_5d 54개가 DEAD_SPARSE 로 죽어 재정의한 event_rolling_features 8개를
모델에 투입하려면 패널에 새 컬럼을 붙여야 한다. 전체 재빌드는 몇 시간이 걸리므로, 검증된
컬럼 패치 방식(patch_panel_asof.py 와 같은 전략)으로 **같은 행·같은 기존 피처 위에** 컬럼만
추가한다 → A/B 통제가 깨끗하다.

as-of 규율 (스펙 §2 "패널 날짜 **이전** 값만")
  패널 행 (종목, D) 에는 event_rolling_features 의 trade_date < D(엄격) 중 **가장 최근 1행**의
  값을 쓴다. build 단계에서 이미 당일 행을 제외하고 계산하지만, 스펙 문구대로 한 거래일 더
  보수적으로 가져간다(20행 평균이라 값 차이는 미미). **값이 없으면 NaN** — 0으로 메우지
  않는다(스펙 §5, 옛 도구의 0 채움 실패 유형 반복 금지). 원천 테이블이 비어 있으면
  전량-NaN 패널이 조용히 만들어지는 실패 유형이므로 **exit 1** 로 거부한다.

실측 근거 (2026-11-20, 호스트 /usr/bin/python3, 조회 명령):
  # import numpy as np; p=np.load('data/panels/panel_prod200_v4.npz', allow_pickle=True)
  #   [print(k, p[k].shape, p[k].dtype) for k in p.files]
  #   → X (61640,213) float32 · feature_names (213,) <U33 · dates (61640,) · codes (61640,) <U6
  #     price (61640,) float64 · unique dates 314 (2025-08-04..2026-11-17) · unique codes 200
  #   → 스펙 "feature_names 길이 213→221" 일치. 신규 8개 이름의 최장이 20자(<U33)라
  #     feature_names dtype(<U33)을 그대로 유지한다.

자기검증 (스펙 §2)
  - --out 이 --panel 과 같은 경로면 **exit 2**(원본 패널 덮어쓰기 금지).
  - 패널에 신규 8개 이름이 이미 있으면 **exit 3**(중복 append 방지).
  - 저장 후 재독해로 **행수 동일** · **기존 컬럼 바이트 동일**(tobytes 비교) · 이름 길이
    n_orig+8 을 확인하고, 신규 컬럼별 nnz/coverage 를 출력한다(자기신고 3분리:
    소스 수신=패널·테이블 행수 / 파서 생성=append 컬럼 / 실제 저장=재독해 대조).

사용 (호스트, cwd=리포 루트):
    /usr/bin/python3 scripts/patch_panel_events.py --panel data/panels/panel_prod200_v4.npz \
        --out data/panels/panel_prod200_v5.npz
"""
from __future__ import annotations

import argparse
import bisect
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

SRC_TABLE = "event_rolling_features"   # build_event_rolling.py 가 만든 신규 테이블(읽기만)
REQUIRED_KEYS = ("X", "feature_names", "dates", "codes", "price")
NEW_COLS = [  # 순서 = 테이블/스펙 §1 산출 순서 그대로
    "event_level_20d", "event_trend_5v20", "event_any_60d",
    "funding_level_20d", "governance_level_20d", "business_level_20d",
    "risk_level_20d", "disclosure_level_20d",
]


def _read_table(uniq_codes):
    """event_rolling_features SELECT(읽기 전용). 반환 code -> (정렬된 날짜 리스트, 값 리스트).

    쿼리: SELECT stock_code, trade_date::text, 8개 컬럼 FROM event_rolling_features
          WHERE stock_code = ANY(%s) ORDER BY stock_code, trade_date
    """
    conn = psycopg2.connect(**PG)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT stock_code, trade_date::text, %s FROM %s "
            "WHERE stock_code = ANY(%%s) ORDER BY stock_code, trade_date"
            % (", ".join(NEW_COLS), SRC_TABLE), (uniq_codes,))
        per = defaultdict(lambda: ([], []))
        for row in cur.fetchall():
            per[str(row[0])][0].append(row[1])
            per[str(row[0])][1].append(row[2:])
    finally:
        conn.close()
    return per


def main(argv=None):
    ap = argparse.ArgumentParser(description="패널에 event_rolling_features 8개 append "
                                             "(스펙 docs/spec_event_rolling.md §2)")
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if os.path.realpath(args.panel) == os.path.realpath(args.out):
        print("[patch_panel_events] --out 이 --panel 과 같은 파일입니다 — 원본 패널 "
              "덮어쓰기 금지(exit 2): %s" % args.panel, file=sys.stderr)
        return 2

    try:
        with np.load(args.panel, allow_pickle=True) as z:
            missing = [k for k in REQUIRED_KEYS if k not in z.files]
            if missing:
                print("[patch_panel_events] 패널에 필수 키 없음: %s (실제: %s)"
                      % (missing, sorted(z.files)), file=sys.stderr)
                return 1
            X = np.asarray(z["X"], dtype=np.float32)
            names_arr = z["feature_names"]
            dates = z["dates"]
            codes = z["codes"]
            price = z["price"]
    except Exception as e:  # noqa: BLE001 - 어떤 실패도 명확한 rc 로
        print("[patch_panel_events] 패널 읽기 실패(%s): %s" % (type(e).__name__, e),
              file=sys.stderr)
        return 1

    names = [str(n) for n in names_arr]
    dup = [n for n in NEW_COLS if n in names]
    if dup:
        print("[patch_panel_events] 패널에 신규 컬럼이 이미 있음 — 중복 append 금지(exit 3): %s"
              % dup, file=sys.stderr)
        return 3
    n_orig = len(names)
    n_rows = X.shape[0]

    uniq_codes = sorted({str(c) for c in codes})
    per = _read_table(uniq_codes)
    n_tbl = sum(len(v[0]) for v in per.values())
    if n_tbl == 0:
        print("[patch_panel_events] %s 에 패널 종목 행이 0건 — 전량-NaN 패널 생성을 "
              "거부합니다(0 채움 금지, exit 1). build_event_rolling.py 를 먼저 실행하세요."
              % SRC_TABLE, file=sys.stderr)
        return 1

    rows_by_code = defaultdict(list)
    for i, c in enumerate(codes):
        rows_by_code[str(c)].append(i)
    add = np.full((n_rows, len(NEW_COLS)), np.nan, dtype=np.float32)
    hit = 0
    for code, idxs in rows_by_code.items():
        ds, vs = per.get(code, (None, None))
        if not ds:
            continue  # 테이블에 이 종목 이력 없음 → NaN 유지(0 채움 금지)
        for i in idxs:
            D = str(dates[i])
            k = bisect.bisect_left(ds, D)   # trade_date < D (패널 날짜 이전 값만, 엄격)
            if k == 0:
                continue  # 그 날짜 이전 값 없음 → NaN 유지
            v = vs[k - 1]
            for j in range(len(NEW_COLS)):
                if v[j] is not None:
                    add[i, j] = float(v[j])
            hit += 1

    X_new = np.concatenate([X, add], axis=1)   # 기존 블록은 복사만 — 수정 없음
    names_new = np.array(names + NEW_COLS, dtype=names_arr.dtype)
    orig_bytes = X.tobytes()
    assert X_new[:, :n_orig].tobytes() == orig_bytes, "기존 컬럼 바이트 불일치(사전 검증)"

    print("[patch_panel_events] 패널 %d행×%d피처 + %s %d행 → 매칭 %d/%d행 (%.1f%%)"
          % (n_rows, n_orig, SRC_TABLE, n_tbl, hit, n_rows, 100.0 * hit / n_rows))
    for j, cname in enumerate(NEW_COLS):
        col = add[:, j]
        # nnz_ratio_honest = feature_survival_report 와 같은 정의: (결측 아님 & !=0)/전체 행.
        # (결측이면 !=0 비교가 True 라 nnz 에 섞인다 — 실측 2026-11-20: 마스크 없이 세면
        #  event_level_20d 가 0.9921 로 나와 생존 리포트의 0.7170(=0.9921-0.2751 null)과
        #  어긋난다. 패널 값은 동일하고 **표시만** 틀렸다.)
        valid = ~np.isnan(col)
        nz = int(np.sum(valid & (col != 0)))
        nn = int(np.sum(~valid))
        print("  %-22s nnz=%7.4f null=%7.4f" % (cname, nz / n_rows, nn / n_rows))

    if args.dry_run:
        print("[patch_panel_events] dry-run — %s 에 쓰지 않았습니다" % args.out)
        return 0

    # savez_compressed: 기존 패널(v3/v4 24.7MB)과 같은 압축 저장 관례(재독해 배열은 동일)
    np.savez_compressed(args.out, X=X_new, feature_names=names_new,
                        dates=dates, codes=codes, price=price)

    # 자기검증: 저장 후 재독해로 행수·기존 컬럼 바이트·이름 길이 대조
    try:
        with np.load(args.out, allow_pickle=True) as z2:
            ok_shape = z2["X"].shape == (n_rows, n_orig + len(NEW_COLS))
            ok_bytes = z2["X"][:, :n_orig].tobytes() == orig_bytes
            ok_names = len(z2["feature_names"]) == n_orig + len(NEW_COLS)
            ok_meta = (np.array_equal(z2["dates"], dates) and np.array_equal(z2["codes"], codes)
                       and np.array_equal(z2["price"], price))
    except Exception as e:  # noqa: BLE001
        print("[patch_panel_events] 재독해 검증 실패(%s): %s" % (type(e).__name__, e),
              file=sys.stderr)
        return 1
    if not (ok_shape and ok_bytes and ok_names and ok_meta):
        print("[patch_panel_events] 자기검증 실패: shape=%s bytes=%s names=%s meta=%s"
              % (ok_shape, ok_bytes, ok_names, ok_meta), file=sys.stderr)
        return 1
    print("[patch_panel_events] 저장: %s (%d행×%d피처) — 자기검증 통과 "
          "(행수 동일, 기존 %d컬럼 바이트 동일, dates/codes/price 동일)"
          % (args.out, n_rows, n_orig + len(NEW_COLS), n_orig))
    return 0


if __name__ == "__main__":
    sys.exit(main())
