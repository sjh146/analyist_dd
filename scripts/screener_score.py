#!/usr/bin/env python3
"""screener_score.py — 종가/스윙/단타 3개 스크리너 통합 승률 채점 러너.

1) ``--screeners`` 로 각 스크리너를 기본 인자 실행 → 후보 CSV 생성
   (data/reports/*_candidates_*.csv).
2) ``--score``(기본) 로 후보 CSV를 스캔해 **아직 채점 안 된** 후보만 market_data에서
   가격을 조회해 각자의 시간창으로 채점한다.
3) ``data/scoring/scored.jsonl`` 레지스트리(append-only)로 **중복 채점을 방지**한다.

창 정의:
  close      : D 종가 매수 → D+1 시가 매도 (갭 업 = 승)
  swing      : D+1 종가 매수 → D+7 종가 매도 (rows[N-1] 오프바이원 주의)
  daytrading : D 종가 → D+1 시가 갭 (분봉 부재 프록시, 30분 창 대비)

사용법:
  python3 scripts/screener_score.py --screeners close,swing,daytrading
  python3 scripts/screener_score.py --score --report-dir data/reports
"""
import argparse
import glob
import json
import logging
import os
import subprocess
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("screener_score")

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

SCORING_DIR = "data/scoring"
DEFAULT_SCREENERS = ["close", "swing", "daytrading"]

# screener → 후보 CSV 파일명 접두사
SCREENER_PREFIX = {
    "close": "close_candidates_",
    "swing": "swing_candidates_",
    "daytrading": "daytrading_candidates_",
}

# screener → 후보 CSV에서 signal_date 컬럼 우선 여부 (없으면 파일명 파싱)
SIGNAL_DATE_COLUMN = {"close": True, "swing": False, "daytrading": True}

# screener → 매수/매도 창 정의 (설명용)
WINDOW_LABEL = {
    "close": "D종가→D+1시가",
    "swing": "D+1종가→D+7종가",
    "daytrading": "D종가→D+1시가(갭프록시)",
}


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def parse_signal_date_from_filename(name_or_path):
    """파일명 *_YYYYMMDD_HHMMSS.csv 에서 날짜 파싱 → datetime.date."""
    import re
    base = os.path.basename(name_or_path)
    m = re.search(r"_(\d{8})_\d{6}\.csv$", base)
    if not m:
        raise ValueError(f"파일명에서 날짜를 파싱할 수 없습니다: {name_or_path}")
    from datetime import datetime
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def normalize_code(code):
    """종목코드를 6자리 숫자 문자열로 정규화 (pandas가 선행 0을 자르는 것 방지)."""
    s = str(code).strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def candidate_signal_date(screener, df, path):
    """후보 CSV의 signal_date(컬럼 우선, 없으면 파일명) → date."""
    if SIGNAL_DATE_COLUMN.get(screener) and "signal_date" in df.columns \
            and not df["signal_date"].isna().all():
        try:
            return pd.to_datetime(str(df["signal_date"].iloc[0])).date()
        except (ValueError, TypeError):
            pass
    return parse_signal_date_from_filename(path)


# ── 가격 조회 헬퍼 (창별) ─────────────────────────────────────────────
def fetch_bars(pg, stock_code, from_date, limit):
    """from_date 이상 trade_date, open/close 정렬 조회 → list[(date, open, close)]."""
    cur = pg.cursor()
    cur.execute(
        "SELECT trade_date, open_price, close_price FROM market_data "
        "WHERE stock_code = %s AND trade_date >= %s ORDER BY trade_date LIMIT %s",
        (stock_code, from_date, int(limit)),
    )
    rows = cur.fetchall()
    cur.close()
    return [(r[0], float(r[1]) if r[1] is not None else None,
             float(r[2]) if r[2] is not None else None) for r in rows]


def score_close(pg, code, signal_date):
    """close: D 종가 매수 → D+1 시가 매도. 승 = D+1 시가 > D 종가."""
    bars = fetch_bars(pg, code, signal_date, 2)
    if len(bars) < 2:
        return None
    d0, d0_open, d0_close = bars[0]
    d1, d1_open, d1_close = bars[1]
    if d0 != signal_date or d0_close is None or d1_open is None or d1_close is None:
        return None
    if d0_close <= 0:
        return None
    ret = (d1_open / d0_close - 1.0) * 100.0
    return {
        "base_close": round(d0_close, 2),
        "sell_price": round(d1_open, 2),
        "sell_date": str(d1),
        "return_pct": round(ret, 2),
        "win": ret > 0,
    }


def score_swing(pg, code, signal_date, horizon=7):
    """swing: D+1 종가 매수 → D+7 종가 매도 (rows[horizon-1] 오프바이원 주의)."""
    bars = fetch_bars(pg, code, signal_date, horizon + 2)
    if len(bars) < 2:
        return None
    d0, _, d0_close = bars[0]
    if d0 != signal_date or d0_close is None or d0_close <= 0:
        return None
    base, base_close = bars[1][0], bars[1][2]
    if base_close is None or base_close <= 0:
        return None
    # base=rows[0]=D+1. D+7 = rows[horizon-1] (오프바이원).
    if len(bars) < horizon + 1:
        return None
    sell_date, _, sell_close = bars[horizon]
    if sell_close is None or sell_close <= 0:
        return None
    ret = (sell_close / base_close - 1.0) * 100.0
    return {
        "base_close": round(base_close, 2),
        "sell_price": round(sell_close, 2),
        "sell_date": str(sell_date),
        "return_pct": round(ret, 2),
        "win": ret > 0,
        "sell_index": horizon,
    }


def score_daytrading(pg, code, signal_date):
    """daytrading: D 종가 → D+1 시가 갭 (분봉 부재 프록시, 30분 창 대비)."""
    bars = fetch_bars(pg, code, signal_date, 2)
    if len(bars) < 2:
        return None
    d0, _, d0_close = bars[0]
    d1, d1_open, _ = bars[1]
    if d0 != signal_date or d0_close is None or d0_close <= 0 or d1_open is None:
        return None
    ret = (d1_open / d0_close - 1.0) * 100.0
    return {
        "base_close": round(d0_close, 2),
        "sell_price": round(d1_open, 2),
        "sell_date": str(d1),
        "return_pct": round(ret, 2),
        "win": ret > 0,
    }


SCORE_FUNC = {
    "close": score_close,
    "swing": score_swing,
    "daytrading": score_daytrading,
}


# ── 후보 스캔 ─────────────────────────────────────────────────────────
def scan_candidates(report_dir, screeners=None):
    """data/reports 에서 지정 스크리너 후보 CSV 로드.

    반환: [(screener, signal_date, stock_code, row, path)]  (중복 코드 제거).
    """
    screeners = screeners or DEFAULT_SCREENERS
    out = []
    for scr in screeners:
        prefix = SCREENER_PREFIX[scr]
        for f in sorted(glob.glob(os.path.join(report_dir, prefix + "*.csv"))):
            try:
                df = pd.read_csv(f)
                if df.empty or "stock_code" not in df.columns:
                    continue
                sig_date = candidate_signal_date(scr, df, f)
                seen = set()
                for _, row in df.iterrows():
                    code = normalize_code(row["stock_code"])
                    if code in seen:
                        continue
                    seen.add(code)
                    out.append((scr, sig_date, code, row, f))
            except Exception as e:
                logger.warning(f"후보 파일 로드 실패 {f}: {e}")
    return out


# ── 레지스트리 ────────────────────────────────────────────────────────
def load_registry(scoring_dir):
    """scored.jsonl 로드 → {key: json}. 파일 없으면 빈 dict."""
    path = os.path.join(scoring_dir, "scored.jsonl")
    reg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    reg[rec["key"]] = rec
                except Exception:
                    continue
    return reg


def registry_key(screener, signal_date, stock_code):
    return f"{screener}|{signal_date}|{stock_code}"


def append_registry(scoring_dir, records):
    """채점 결과를 scored.jsonl에 append. records는 key 포함 dict 리스트."""
    os.makedirs(scoring_dir, exist_ok=True)
    path = os.path.join(scoring_dir, "scored.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 요약 집계 ─────────────────────────────────────────────────────────
def _stats(values):
    if not values:
        return {"win_rate": 0.0, "avg_return_pct": 0.0, "median_return_pct": 0.0,
                "max_return_pct": 0.0, "min_return_pct": 0.0}
    arr = np.array(values, dtype=float)
    wins = int(np.sum(arr > 0))
    return {
        "win_rate": round(wins / len(arr) * 100.0, 1),
        "avg_return_pct": round(float(np.mean(arr)), 2),
        "median_return_pct": round(float(np.median(arr)), 2),
        "max_return_pct": round(float(np.max(arr)), 2),
        "min_return_pct": round(float(np.min(arr)), 2),
    }


def summarize_per_screener(results):
    """results: 스크리너별 결과 dict 리스트 → 스크리너별 통계."""
    stats = {}
    by = {}
    for r in results:
        by.setdefault(r["screener"], []).append(r["return_pct"])
    for scr, vals in by.items():
        s = _stats(vals)
        stats[scr] = {
            "win_rate": s["win_rate"],
            "avg_return_pct": s["avg_return_pct"],
            "median_return_pct": s["median_return_pct"],
            "max_return_pct": s["max_return_pct"],
            "min_return_pct": s["min_return_pct"],
            "sample_count": len(vals),
            "window": WINDOW_LABEL[scr],
        }
    return stats


# ── 스크리너 실행 ─────────────────────────────────────────────────────
def run_screeners(screeners, scripts_dir):
    """각 스크리너 CLI를 기본 인자로 실행해 후보 CSV 생성."""
    runner_map = {
        "close": ["close_screener.py", "--top-n", "20"],
        "swing": ["swing_screener.py"],
        "daytrading": ["daytrading_screener.py", "--top-n", "20"],
    }
    ran = []
    for scr in screeners:
        script_args = runner_map[scr]
        script = os.path.join(scripts_dir, script_args[0])
        if not os.path.exists(script):
            logger.warning(f"스크립트 없음: {script} — skip {scr}")
            continue
        cmd = [sys.executable, script] + [a for a in script_args[1:]]
        logger.info(f"스크리너 실행: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, cwd=scripts_dir)
            ran.append(scr)
        except subprocess.CalledProcessError as e:
            logger.warning(f"{scr} 실행 실패 (exit {e.returncode}): {e}")
    return ran


# ── 채점 메인 ─────────────────────────────────────────────────────────
def score_candidates(pg, candidates, registry, scoring_dir):
    """후보 중 레지스트리에 없는 것만 채점 → (결과 dict 리스트, 점수됨 수, 스킵 수)."""
    results = []
    appended = []
    scored = skipped = 0
    now = pd.Timestamp.now().isoformat()
    for screener, signal_date, code, row, path in candidates:
        key = registry_key(screener, signal_date, code)
        if key in registry:
            skipped += 1
            continue
        func = SCORE_FUNC[screener]
        try:
            info = func(pg, code, signal_date)
        except Exception as e:
            logger.debug(f"{key} 조회 실패: {e}")
            info = None
        if info is None:
            continue  # 창 미경과 / 필요 가격 없음 → 이번엔 미채점, 다음에 재시도
        rec = {
            "key": key,
            "screener": screener,
            "signal_date": str(signal_date),
            "stock_code": code,
            "stock_name": str(row.get("stock_name", "")),
            "sector": str(row.get("sector", "")),
            "scored_at": now,
            "return_pct": info["return_pct"],
            "win": info["win"],
            "base_close": info["base_close"],
            "sell_price": info["sell_price"],
            "sell_date": info["sell_date"],
            "window": WINDOW_LABEL[screener],
        }
        results.append(rec)
        appended.append(rec)
        registry[key] = rec
        scored += 1
    if appended:
        append_registry(scoring_dir, appended)
    return results, scored, skipped


def write_outputs(scoring_dir, report_dir, results, scored, skipped):
    """상세 CSV + 요약 JSON 저장 → (csv_path, summary_path)."""
    os.makedirs(scoring_dir, exist_ok=True)
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    csv_path = None
    if results:
        csv_path = os.path.join(scoring_dir, f"results_{ts}.csv")
        pd.DataFrame(results).to_csv(csv_path, index=False)
    per = summarize_per_screener(results)
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "total_scored": scored,
        "skipped_existing": skipped,
        "screener_stats": per,
    }
    if not os.path.isabs(report_dir):
        report_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", report_dir)
    report_dir = os.path.normpath(report_dir)
    os.makedirs(report_dir, exist_ok=True)
    summary_path = os.path.join(report_dir, "scoring_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return csv_path, summary_path


def main():
    ap = argparse.ArgumentParser(
        description="종가/스윙/단타 스크리너 통합 승률 채점 러너")
    ap.add_argument("--screeners", default=",".join(DEFAULT_SCREENERS),
                    help=f"채점할 스크리너 (기본 {','.join(DEFAULT_SCREENERS)})")
    ap.add_argument("--score", action="store_true", default=True,
                    help="후보 채점 수행 (기본 켜짐)")
    ap.add_argument("--no-score", dest="score", action="store_false",
                    help="스크리너 실행만 수행, 채점 생략")
    ap.add_argument("--scripts-dir", default=None, help="스크리너 스크립트 디렉토리 (기본: scripts/)")
    ap.add_argument("--report-dir", default="data/reports", help="후보 CSV 디렉토리")
    ap.add_argument("--scoring-dir", default=SCORING_DIR, help="레지스트리/상세 출력 디렉토리")
    ap.add_argument("--swing-horizon", type=int, default=7,
                    help="스윙 보유 거래일 (기본 7)")
    args = ap.parse_args()

    screeners = [s.strip() for s in args.screeners.split(",") if s.strip()]
    for s in screeners:
        if s not in SCREENER_PREFIX:
            logger.error(f"알 수 없는 스크리너: {s} (가능: {', '.join(DEFAULT_SCREENERS)})")
            sys.exit(1)

    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    scripts_dir = os.path.normpath(
        args.scripts_dir if args.scripts_dir else
        os.path.join(os.path.dirname(os.path.abspath(__file__))))
    report_dir = args.report_dir
    if not os.path.isabs(report_dir):
        report_dir = os.path.normpath(os.path.join(repo, report_dir))
    scoring_dir = args.scoring_dir
    if not os.path.isabs(scoring_dir):
        scoring_dir = os.path.normpath(os.path.join(repo, scoring_dir))

    run_screeners(screeners, scripts_dir)

    if not args.score:
        logger.info("--no-score: 채점 생략")
        return

    candidates = scan_candidates(report_dir, screeners)
    logger.info(f"후보 스캔: {len(candidates)} 건 (스크리너 {', '.join(screeners)})")
    if not candidates:
        logger.info("채점할 후보 없음")
        return

    registry = load_registry(scoring_dir)
    pg = get_pg_conn()
    try:
        results, scored, skipped = score_candidates(
            pg, candidates, registry, scoring_dir)
    finally:
        try:
            pg.close()
        except Exception:
            pass

    csv_path, summary_path = write_outputs(
        scoring_dir, report_dir, results, scored, skipped)
    logger.info(f"채점 완료: 신규 {scored}, 중복스킵 {skipped}")

    print("\n" + "=" * 60)
    print("스크리너 승률 채점 요약")
    print("=" * 60)
    if results:
        per = summarize_per_screener(results)
        for scr, s in per.items():
            print(f"  {scr:12s} {WINDOW_LABEL[scr]:22s} 채점 {s['sample_count']:>3} | "
                  f"승률 {s['win_rate']}% 평균 {s['avg_return_pct']}%")
    else:
        print("  (신규 채점 건 없음 — 모두 레지스트리에 존재하거나 창 미경과)")
    print("-" * 60)
    print(f"신규 채점 {scored} | 중복 스킵 {skipped}")
    if csv_path:
        print(f"상세 CSV: {csv_path}")
    print(f"요약 JSON: {summary_path}")


if __name__ == "__main__":
    main()
