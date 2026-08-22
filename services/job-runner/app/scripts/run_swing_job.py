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
from datetime import date, datetime


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
    return "up" if c.get("prob", 0.5) >= 0.5 else "down"


def main():
    screener = "/opt/scripts/swing_screener.py"
    out_csv = "/tmp/swing_out.csv"
    if os.path.exists(out_csv):
        os.remove(out_csv)

    cmd = [
        sys.executable, screener,
        "--include-krx-data", "--include-economic-events",
        "--output", out_csv,
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
        "date": date.today().isoformat(),
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
