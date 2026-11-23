#!/usr/bin/env python3
"""온디맨드 스윙 스크리너 잡 — champion 모델로 KOSDAQ 전체 스크리닝.

swing_screener.py를 서브프로세스로 실행 (기존 파이프라인과 동일 코드 경로).
출력 CSV → swing_latest.json 형식으로 변환 → reports/swing_latest.json 기록.
stdout 마지막 줄: 결과 JSON.
"""
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

KST = timedelta(hours=9)


def _signal_date() -> str:
    """시그널 기준일 = **최근 거래일**(market_data 의 max(trade_date)).

    WHY(실측 2026-11-23 08:31 KST): 이 컨테이너의 TZ 는 UTC 라 `date.today()` 가
    **전날**을 가리킨다 — 월요일 08:30 실행이 `date=2026-11-22(일요일)` 로 기록돼
    swing 피드 20건의 signal_date 가 비거래일로 나갔다(피드 계약: signal_date 는
    신호의 기준 거래일). 달력일이 아니라 데이터가 가리키는 날짜를 쓴다.
    DB 를 못 읽으면 **KST 달력일**로 폴백한다(UTC 날짜로 되돌아가지 않는다).
    """
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
            user=os.environ.get("POSTGRES_USER", "stock_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max(trade_date) FROM market_data")
                row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return row[0].isoformat()
    except Exception as exc:  # noqa: BLE001 — 날짜 계산 실패가 잡 전체를 죽이면 안 된다
        sys.stderr.write(f"[swing_job] signal_date DB 조회 실패({exc}) → KST 달력일 폴백\n")
    return (datetime.utcnow() + KST).date().isoformat()


def _row_to_candidate(row: dict) -> dict:
    c = {}
    for k, v in row.items():
        k = k.strip()
        if not k or k == "low_dim_vec":  # numpy 배열 repr 컬럼 제외
            continue
        if k == "stock_code":
            # CSV에서 숫자로 내려온 코드를 6자리 문자열로 복원 (036800 → 36800.0 문제)
            try:
                c[k] = f"{int(float(v)):06d}"
            except (TypeError, ValueError):
                c[k] = str(v)
            continue
        try:
            c[k] = float(v)
        except (TypeError, ValueError):
            c[k] = v
    return c


def _direction(c: dict) -> str:
    d = str(c.get("dir", "")).lower()
    if d in ("up", "down"):
        return d
    # CSV 는 confidence(0~1) 를 쓴다 — 없는 키 'prob' 를 보면 항상 'up' 이 된다.
    conf = c.get("confidence", c.get("prob"))
    return "up" if (conf if conf is not None else 0.5) >= 0.5 else "down"


def main():
    screener = "/opt/scripts/swing_screener.py"
    out_csv = "/tmp/swing_out.csv"
    if os.path.exists(out_csv):
        os.remove(out_csv)

    # 전체 스코어 유니버스 덤프 경로(피드 계약 v1.1 rank_pct 근거). /app/reports 는
    # 호스트 reports/ 로 마운트돼 있어 분석측이 바로 읽는다. 파일명과 payload 의 date 는
    # **같은 값(최근 거래일)** 을 쓴다 — 2026-11-23 실측: 컨테이너 TZ(UTC)의 date.today() 가
    # 월요일 08:30 실행에서 일요일(11-22)을 찍어 signal_date 가 비거래일이 됐다.
    signal_date = _signal_date()
    universe_path = f"/app/reports/swing_universe_{signal_date}.json"

    cmd = [
        sys.executable, screener,
        "--include-krx-data", "--include-economic-events",
        "--output", out_csv,
        "--universe-output", universe_path,
    ]
    proc = subprocess.run(
        cmd, cwd="/opt/xgboost-ml",
        env=dict(os.environ), capture_output=True, text=True, timeout=2700,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-3000:] + "\n" + proc.stderr[-3000:])
        sys.exit(1)

    candidates = []
    if os.path.exists(out_csv):
        with open(out_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                candidates.append(_row_to_candidate(row))

    auc = "n/a"
    try:
        with open("/opt/xgboost-ml/app/models/champion/auc.txt") as f:
            auc = f.read().strip()
    except OSError:
        pass

    up = [c for c in candidates if _direction(c) == "up"]
    down = [c for c in candidates if _direction(c) == "down"]

    # 배치 타입 (swing_screener가 CSV에 기록 — signal=0.55 이상 실시그널 존재)
    batch_type = (candidates[0].get("batch_type") if candidates else None) or "unknown"

    result = {
        "request_type": "swing_screener",
        "date": signal_date,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total": len(candidates),
        "up": len(up),
        "down": len(down),
        "high_confidence": sum(1 for c in candidates if c.get("confidence", 0) >= 0.65),
        "batch_type": batch_type,
        "auc": auc,
        "top_up": up[:20],
        "top_down": down[:20],
        "candidates": candidates[:50],
    }

    reports_dir = os.getenv("REPORTS_DIR", "/app/reports")
    os.makedirs(reports_dir, exist_ok=True)
    with open(os.path.join(reports_dir, "swing_latest.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
