#!/usr/bin/env python3
"""온디맨드 종가스크리너 잡 — 종가배팅 스크리너 실행.

close_screener.py를 서브프로세스로 실행 (기존 파이프라인과 동일 코드 경로).
출력 CSV → close_latest.json 형식으로 변환 → reports/close_latest.json 기록.
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
        if not k:
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


def attach_screener_meta(result: dict, out_csv: str) -> dict:
    """R26: 스크리너 sidecar(<csv>.meta.json)를 발행물 메타로 옮긴다.

    close_screener.py 가 남기는 상한가·급등 제외 건수 등은 CSV 로는 전달되지 않으므로
    sidecar 를 읽어 result["screener_meta"] 로 붙인다(없으면 그대로 반환 — 하위호환).
    """
    meta_path = out_csv + ".meta.json"
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                result["screener_meta"] = json.load(f)
        except (OSError, ValueError):
            pass
    return result


def main():
    screener = "/opt/scripts/close_screener.py"
    out_csv = "/tmp/close_out.csv"
    if os.path.exists(out_csv):
        os.remove(out_csv)

    cmd = [sys.executable, screener, "--top-n", "20", "--output", out_csv]
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

    result = {
        "request_type": "close_screener",
        "date": date.today().isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total": len(candidates),
        "candidates": candidates[:50],
    }
    # R26: 스크리너 sidecar(상한가·급등 제외 건수 등)를 발행물 메타로 옮긴다.
    attach_screener_meta(result, out_csv)

    reports_dir = os.getenv("REPORTS_DIR", "/app/reports")
    os.makedirs(reports_dir, exist_ok=True)
    with open(os.path.join(reports_dir, "close_latest.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
