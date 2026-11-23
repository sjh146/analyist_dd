"""피처 커버리지 리포트 — 죽은 피처(상수 0) 조기 발견.

WHY: 173피처 중 상당수가 항상 0(상수)인데도 이를 알아챌 신호가 없었다.
이 스크립트는 훈련 패널의 각 피처에 대해 nonzero 비율과 표준편차를 계산해
``feature_coverage`` 테이블에 upsert한다. postgres-exporter 커스텀 쿼리가 이
테이블을 읽어 ``feature_dead_count`` / ``feature_alive_count`` 메트릭으로
노출하므로, Prometheus/Grafana 에서 시계열로 추적할 수 있다.

실행 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python scripts/feature_coverage_report.py

패널 캐시(app/models/exp_panel)가 있으면 읽기만 하고 재사용한다(다른 에이전트가
쓰고 있을 수 있으므로 쓰기 금지). 캐시가 없으면 소량(30종목)으로 축소 빌드한다.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 패널에서 진단/식별자로 취급해 피처 집계에서 제외할 컬럼
ID_COLUMNS = {"stock_code", "date", "feature_count"}

PANEL_CACHE = os.path.join("app", "models", "exp_panel", "panel.pkl")
PANEL_META = os.path.join("app", "models", "exp_panel", "meta.json")
PANEL_STALE_DAYS = int(os.environ.get("FEATURE_COVERAGE_PANEL_STALE_DAYS", "10"))

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feature_coverage (
    feature_name TEXT PRIMARY KEY,
    nonzero_ratio DOUBLE PRECISION NOT NULL,
    std DOUBLE PRECISION NOT NULL,
    window_days INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# 2026-09-25: 정확성 컬럼 추가(미적용 DB 에서도 동작하도록 ALTER 를 먼저 건다).
ALTER_TABLE_SQL = """
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_naive DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_honest DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS null_ratio DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS stock_unique_median DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS cross_section_constant_ratio DOUBLE PRECISION;
"""

UPSERT_SQL = """
INSERT INTO feature_coverage (
    feature_name, nonzero_ratio, std, window_days, computed_at,
    nonzero_ratio_naive, nonzero_ratio_honest, null_ratio,
    stock_unique_median, cross_section_constant_ratio
)
VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s)
ON CONFLICT (feature_name) DO UPDATE SET
    nonzero_ratio = EXCLUDED.nonzero_ratio,
    std = EXCLUDED.std,
    window_days = EXCLUDED.window_days,
    computed_at = EXCLUDED.computed_at,
    nonzero_ratio_naive = EXCLUDED.nonzero_ratio_naive,
    nonzero_ratio_honest = EXCLUDED.nonzero_ratio_honest,
    null_ratio = EXCLUDED.null_ratio,
    stock_unique_median = EXCLUDED.stock_unique_median,
    cross_section_constant_ratio = EXCLUDED.cross_section_constant_ratio
"""


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)


def _app_roots():
    """`app` 패키지가 있을 수 있는 루트 후보 (컨테이너 /app → 호스트 repo/services/xgboost-ml).

    WHY (2026-11-24 실측): R49 가 호스트에서 `--ignore-cache` 로 돌 때
    `ModuleNotFoundError: No module named 'app'` 으로 죽었다. 후보가 "/app" 과 cwd 뿐이라
    호스트에서 services/xgboost-ml/app 을 못 찾았다 — 재측정 경로 자체가 막혀
    feature_coverage 가 5일(그리고 계속) 정체했다.
    """
    cands = ["/app", os.getcwd(), os.path.join(_REPO_ROOT, "services", "xgboost-ml")]
    out = []
    for p in cands:
        if p and p not in out:
            out.append(p)
    return out


def _ensure_app_on_path():
    for p in _app_roots():
        if p and p not in sys.path:
            sys.path.insert(0, p)


def _panel_files():
    """(panel.pkl, meta.json) 실제 경로 — 존재하는 루트를 고른다(없으면 cwd 기준 기본값)."""
    rel = os.path.join("app", "models", "exp_panel")
    for root in _app_roots():
        d = os.path.join(root, rel)
        if os.path.exists(os.path.join(d, "panel.pkl")):
            return os.path.join(d, "panel.pkl"), os.path.join(d, "meta.json")
    return PANEL_CACHE, PANEL_META


def _load_panel_cached():
    """exp_panel 캐시(있으면)를 읽기 전용으로 로드. (panel_df, window_days) 반환.

    캐시가 없으면 (None, None) 반환.
    """
    panel_cache, panel_meta = _panel_files()
    if not (os.path.exists(panel_cache) and os.path.exists(panel_meta)):
        return None, None
    try:
        window_days = 120
        with open(panel_meta, "r", encoding="utf-8") as f:
            meta = json.load(f)
            window_days = int(meta.get("days", window_days))
        df = pd.read_pickle(panel_cache)
        return df, window_days
    except Exception as e:  # noqa: BLE001
        print(f"[feature_coverage] exp_panel 캐시 로드 실패(무시): {e}")
        return None, None


def _panel_last_trade_date():
    """exp_panel meta 의 last_trade_date (판단 불가면 None)."""
    try:
        _, panel_meta = _panel_files()
        with open(panel_meta, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("last_trade_date")
    except (OSError, ValueError):
        return None


def panel_staleness(last_trade_date, today=None, stale_days=None):
    """패널 마지막 거래일과 오늘의 간격(달력일). 판단 불가면 None.

    WHY (2026-11-23 실측): `app/models/exp_panel` 캐시는 2026-10-06 에 만들어졌는데,
    이 리포트를 그대로 돌리면 **10-06 패널로 측정한 커버리지**를 `computed_at=now()` 로
    upsert 한다 — `dq_feature_oldest_age_days` 가 0 으로 리셋돼 '오늘 재측정'처럼 보이는
    **신선도 위조**다(실제로는 6주 전 값). 그래서 낡은 패널이면 쓰기를 거부하고 exit 3.
    """
    if not last_trade_date:
        return None
    try:
        last = datetime.strptime(str(last_trade_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = today or datetime.now().date()
    return (today - last).days


def _build_small_panel(pg, n_stocks=30, days=30):
    """캐시 부재 시 소량 축소 빌드 (대형 학습/빌드 아님)."""
    _ensure_app_on_path()
    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.training.universe import select_training_universe

    stocks = select_training_universe(pg, limit=n_stocks, min_days=30, seed=0)
    pipeline = FeaturePipeline(pg_conn=pg)
    end = datetime.now()
    start = end - timedelta(days=days)
    df = pipeline.build_training_features(
        stocks, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if df is None or len(df) == 0:
        raise RuntimeError("축소 패널 빌드 실패 (결과 없음)")
    return df, days


def compute_coverage(df, window_days):
    """각 피처의 커버리지/정확성 통계 계산.

    2026-09-25 확장 — 기존 nonzero_ratio 하나로는 "NaN 을 값으로 세는" 착시를 알 수 없었다.
    pandas/numpy 에서 ``col != 0`` 은 NaN 을 True 로 평가하므로, 결측이 많은 피처가
    커버리지 99% 로 보고된다(실측: SNS 피처). 그래서 naive(착시)와 honest(정직)를 함께
    저장하고, 상수/시장레벨 판정 통계도 같이 남긴다.
    """
    rows = []
    has_stock = "stock_code" in df.columns
    has_date = "date" in df.columns
    for col in df.columns:
        if col in ID_COLUMNS:
            continue
        num = pd.to_numeric(df[col], errors="coerce")
        n = len(num)
        # naive: `!= 0` 그대로 (NaN != 0 → True 이므로 결측이 값으로 세어진다)
        naive = float((num != 0).sum()) / n if n else 0.0
        # honest: 결측 제외 후 비영
        honest = float(((num.notna()) & (num != 0)).sum()) / n if n else 0.0
        null_ratio = float(num.isna().sum()) / n if n else 0.0
        filled = num.fillna(0.0)
        nonzero = int((filled != 0).sum())
        ratio = (nonzero / n) if n else 0.0
        std = float(filled.std(ddof=0)) if n else 0.0
        # 종목별 (비결측 유니크값 개수) 의 중앙값 → 1 이하면 종목 상수(=룩어헤드 의심)
        if has_stock and n:
            per_stock = num.groupby(df["stock_code"]).nunique()
            stock_unique_median = float(per_stock.median()) if len(per_stock) else 0.0
        else:
            stock_unique_median = 0.0
        # 날짜별 종목간 유니크값이 1 이하인 날짜의 비율 → 1.0 이면 시장레벨(횡단면 무변별)
        if has_date and n:
            per_date = num.groupby(df["date"]).nunique()
            cross_section_constant_ratio = (
                float(per_date.le(1).mean()) if len(per_date) else 0.0
            )
        else:
            cross_section_constant_ratio = 0.0
        rows.append({
            "feature_name": col,
            "nonzero_ratio": round(float(ratio), 6),
            "std": round(float(std), 6),
            "window_days": int(window_days),
            "nonzero_ratio_naive": round(naive, 6),
            "nonzero_ratio_honest": round(honest, 6),
            "null_ratio": round(null_ratio, 6),
            "stock_unique_median": round(stock_unique_median, 4),
            "cross_section_constant_ratio": round(cross_section_constant_ratio, 4),
        })
    return rows


def upsert_coverage(pg, rows):
    cur = pg.cursor()
    try:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(ALTER_TABLE_SQL)
        for r in rows:
            cur.execute(
                UPSERT_SQL,
                (
                    r["feature_name"], r["nonzero_ratio"], r["std"], r["window_days"],
                    r["nonzero_ratio_naive"], r["nonzero_ratio_honest"], r["null_ratio"],
                    r["stock_unique_median"], r["cross_section_constant_ratio"],
                ),
            )
        pg.commit()
    finally:
        cur.close()


def recent_measurement(pg, window_days, hours):
    """(최근 hours 내 재측정된 행수, 해당 창 전체 행수). 재빌드 생략 판정용.

    WHY (2026-11-24): 재측정 1회가 60종목/120일 축소 빌드라 약 25분 걸린다. backlog check 가
    rank_* 6행 때문에 0 이 되지 않아 매 틱(2시간)마다 같은 25분 빌드를 반복하면 DB 부하만
    쌓이고 값은 그대로다 — '이미 신선하면 다시 재지 않는다'를 러너가 스스로 판단하게 한다.
    """
    cur = pg.cursor()
    try:
        cur.execute(
            "SELECT count(*) FILTER (WHERE computed_at > now() - (%s || ' hours')::interval),"
            " count(*) FROM feature_coverage WHERE window_days = %s",
            (str(float(hours)), int(window_days)),
        )
        row = cur.fetchone() or (0, 0)
        return int(row[0] or 0), int(row[1] or 0)
    finally:
        cur.close()


def main():
    """커버리지를 재측정해 upsert 하고 판정용 요약을 남긴다.

    낡음 가드는 **캐시를 쓴 경우에만** 적용한다(2026-11-24 수리): `--ignore-cache` 로 방금 새로
    빌드한 신선 패널에도 가드가 걸려 exit 3 으로 쓰기를 거부해, 재측정 경로가 항상 막혀 있었다
    (R49 rc=1/3 의 두 번째 원인). `--json-out` 요약이 없으면 rc=0 완주도 원장엔 '판정불가'만
    남으므로, 구동기의 metric='feature_coverage' 파서가 이 파일을 읽는다.
    """
    ap = argparse.ArgumentParser(description="피처 커버리지 리포트")
    ap.add_argument("--ignore-cache", action="store_true",
                    help="exp_panel 캐시를 무시하고 지금 새로 축소 패널을 빌드한다 "
                         "(캐시가 있으면 기본은 캐시 재사용 — 캐시가 낡았으면 결과도 낡는다)")
    ap.add_argument("--stocks", type=int, default=30, help="축소 패널 종목 수")
    ap.add_argument("--days", type=int, default=30, help="축소 패널 기간(일)")
    ap.add_argument("--max-panel-age-days", type=int, default=PANEL_STALE_DAYS,
                    help="패널 캐시 마지막 거래일이 이만큼 낡으면 쓰기를 거부(신선도 위조 방지)")
    ap.add_argument("--allow-stale", action="store_true",
                    help="낡은 패널이어도 강제로 기록(권장하지 않음 — computed_at 이 거짓말이 된다)")
    ap.add_argument("--json-out", default=None,
                    help="판정용 요약 JSON 경로(구동기 metric='feature_coverage' 가 읽는다)")
    ap.add_argument("--skip-if-fresh-hours", type=float, default=0.0,
                    help="이 시간 안에 같은 창(window_days) 측정이 이미 있으면 재빌드를 생략한다 "
                         "(0=비활성). 25분짜리 재측정을 매 틱 반복하지 않기 위한 가드")
    args = ap.parse_args()

    pg = _pg_connect()
    try:
        if args.skip_if_fresh_hours > 0:
            fresh, total = recent_measurement(pg, args.days, args.skip_if_fresh_hours)
            if fresh >= 50 and total > 0 and fresh >= 0.9 * total:
                print(f"[feature_coverage] skip: window_days={args.days} 측정 {fresh}/{total}행이 "
                      f"{args.skip_if_fresh_hours}h 안에 이미 갱신됨 — 재빌드 생략(신선도 유지)")
                if args.json_out:
                    with open(args.json_out, "w", encoding="utf-8") as f:
                        json.dump({"skipped": True, "fresh_rows": fresh, "total_rows": total,
                                   "window_days": args.days, "from_cache": False}, f,
                                  ensure_ascii=False)
                return 0

        df, window_days = None, None
        from_cache = False
        if not args.ignore_cache:
            df, window_days = _load_panel_cached()
            from_cache = df is not None
        source = "exp_panel 캐시"
        if df is None:
            df, window_days = _build_small_panel(pg, n_stocks=args.stocks, days=args.days)
            source = f"축소 빌드({args.stocks}종목/{args.days}일)"

        age = panel_staleness(_panel_last_trade_date(), stale_days=args.max_panel_age_days) \
            if from_cache else None
        if age is not None and age > args.max_panel_age_days:
            msg = (f"[feature_coverage] 패널 캐시 마지막 거래일이 {age}일 전 "
                   f"(한도 {args.max_panel_age_days}일) — 이대로 쓰면 computed_at 이 "
                   f"'오늘 측정'으로 위조된다. 쓰기 생략(exit 3). "
                   f"갱신: --ignore-cache 로 축소 재빌드 또는 패널 리빌드 후 재실행.")
            print(msg)
            if not args.allow_stale:
                return 3
            print("[feature_coverage] --allow-stale: 그래도 기록한다(값은 낡은 패널 기준).")

        rows = compute_coverage(df, window_days)
        dead = [r for r in rows if r["nonzero_ratio"] == 0]
        alive = [r for r in rows if r["nonzero_ratio"] > 0]
        upsert_coverage(pg, rows)

        print(f"[feature_coverage] source={source} window_days={window_days} "
              f"features={len(rows)}")
        print(f"[feature_coverage] 죽은 피처(=nonzero_ratio 0) {len(dead)}개 / "
              f"살아있는 피처 {len(alive)}개")
        if dead:
            print(f"[feature_coverage] 죽은 피처 목록({len(dead)}): "
                  + ", ".join(sorted(r["feature_name"] for r in dead)))

        # 정확성 신호 (2026-09-25) — 행/값이 있어도 틀린 피처를 드러낸다.
        const = [r for r in rows if (r["stock_unique_median"] or 0) <= 1]
        mkt = [r for r in rows if (r["cross_section_constant_ratio"] or 0) >= 0.99]
        illusion = max((r["nonzero_ratio_naive"] - r["nonzero_ratio_honest"]) for r in rows) if rows else 0.0
        worst_null = max((r["null_ratio"] or 0) for r in rows) if rows else 0.0
        print(f"[feature_coverage] 종목 상수(룩어헤드 의심) {len(const)}개 / "
              f"시장레벨(횡단면 무변별) {len(mkt)}개")
        print(f"[feature_coverage] 커버리지 착시 최대 {illusion:.4f} / 최대 결측비율 {worst_null:.4f}")
        if const:
            print(f"[feature_coverage] 종목 상수 목록({len(const)}): "
                  + ", ".join(sorted(r["feature_name"] for r in const)))
        if args.json_out:
            summary = {
                "metric": "feature_coverage",
                "source": source,
                "from_cache": from_cache,
                "window_days": window_days,
                "panel_last_trade_date": _panel_last_trade_date(),
                "n_features": len(rows),
                "n_dead": len(dead),
                "n_alive": len(alive),
                "n_stock_constant": len(const),
                "n_market_level": len(mkt),
                "illusion_max": round(float(illusion), 6),
                "worst_null_ratio": round(float(worst_null), 6),
                "dead_features": sorted(r["feature_name"] for r in dead),
                "stale_guard": "캐시 사용 시에만 적용(신선 재빌드는 통과)",
                "measured_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            print(f"[feature_coverage] 요약 기록: {args.json_out}")
        return 0
    finally:
        pg.close()


if __name__ == "__main__":
    sys.exit(main())
