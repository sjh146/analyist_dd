#!/usr/bin/env python3
"""피처 생존 점검 — 수집·학습 전에 '죽은 피처'를 매일 확인한다.

판정(feature_coverage 스냅샷 기준):
  * alive    : nonzero_ratio >= 1% 그리고 종목간 상수비율 <= 99%
  * sparse   : 0.05% <= nonzero < 1% (희소 이벤트 — 정상일 수 있음)
  * constant : 종목간 상수비율 > 99% (시장공통 → 순위 판별력 없음)
  * dead     : nonzero_ratio < 0.05% (원천 결측/계산 실패)
추가:
  * 스냅샷 신선도(2일 초과면 STALE_SNAPSHOT)
  * 어제 리포트 대비 **새로 죽은 피처**(NEW_DEAD) 목록 — 회귀 감지
산출: reports/feature_liveness_latest.json, reports/feature_liveness_YYYYMMDD.json, 로그 1줄
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess

REPO = "/home/jhshi/analyist_dd"
REPORTS = os.path.join(REPO, "reports")
LATEST = os.path.join(REPORTS, "feature_liveness_latest.json")


def kst() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))


def _atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp.{0}".format(os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def dotenv() -> dict:
    env = {}
    path = os.path.join(REPO, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip()
    return env


def coverage_rows() -> list[tuple]:
    env = dotenv()
    sql = ("select feature_name, nonzero_ratio_naive, cross_section_constant_ratio, "
           "computed_at from feature_coverage")
    cmd = ["docker", "exec", "-e", "PGPASSWORD=" + env.get("POSTGRES_PASSWORD", ""),
           "stock_postgres", "psql", "-U", env.get("POSTGRES_USER", "stock_user"),
           "-d", env.get("POSTGRES_DB", "stock_trading"), "-t", "-A", "-F", "|", "-c", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        try:
            rows.append((parts[0], float(parts[1] or 0), float(parts[2] or 0), parts[3]))
        except ValueError as exc:
            print("coverage 행 파싱 실패:", parts[0], type(exc).__name__)
            continue
    return rows


STRUCTURAL_UNAVAILABLE = {
    "etf_flow_5d": "per-stock ETF 자금 원천 없음(비ETF 종목엔 의미 없음)",
    "margin_balance_change": "종목별 신용잔고 원천 없음(공개 API 미제공)",
    "credit_balance_change": "종목별 신용잔고 원천 없음(공개 API 미제공)",
}


def classify_feature(nonzero: float, const: float, name: str = "") -> str:
    """판정 우선순위: unavailable(원천 부재) → dead(nonzero<0.0005) → sparse(<0.01) → constant(const>0.99) → alive."""
    if name in STRUCTURAL_UNAVAILABLE:
        return "unavailable"
    if nonzero < 0.0005:
        return "dead"
    if nonzero < 0.01:
        return "sparse"
    if const > 0.99:
        return "constant"
    return "alive"


def main() -> int:
    rows = coverage_rows()
    if not rows:
        print("feature_coverage 조회 실패")
        return 2

    buckets = {"alive": [], "sparse": [], "constant": [], "dead": [], "unavailable": []}
    for name, nz, const, _ in rows:
        buckets[classify_feature(nz, const, name)].append(name)
    alive = buckets["alive"]
    sparse = buckets["sparse"]
    constant = buckets["constant"]
    dead = buckets["dead"]
    unavailable = buckets["unavailable"]

    now = kst()
    today = now.date().isoformat()
    snap = max((r[3] for r in rows), default="")
    stale_snapshot = False
    try:
        snap_dt = datetime.datetime.fromisoformat(snap)
        stale_snapshot = (now - snap_dt).days >= 2
    except Exception:
        stale_snapshot = True

    prev_dead = set()
    prev_path = os.path.join(REPORTS, "feature_liveness_prev.json")
    if os.path.exists(prev_path):
        try:
            prev_dead = set(json.load(open(prev_path, encoding="utf-8")).get("dead") or [])
        except Exception:
            prev_dead = set()
    new_dead = sorted(set(dead) - prev_dead) if prev_dead else []

    miss_path = os.path.join(REPO, "data", "state", "feature_miss_report.json")
    miss_stale_days = None
    miss_report = {}
    if os.path.exists(miss_path):
        miss_stale_days = round((now.timestamp() - os.path.getmtime(miss_path)) / 86400.0, 1)
        try:
            miss_report = json.load(open(miss_path, encoding="utf-8"))
        except Exception:
            miss_report = {}

    report = {
        "at": now.isoformat(timespec="seconds"),
        "snapshot": snap,
        "stale_snapshot": stale_snapshot,
        "counts": {"total": len(rows), "alive": len(alive), "sparse": len(sparse),
                   "constant": len(constant), "dead": len(dead), "unavailable": len(unavailable)},
        "dead": dead,
        "sparse": sparse,
        "constant": constant,
        "unavailable": {n: STRUCTURAL_UNAVAILABLE[n] for n in unavailable},
        "new_dead": new_dead,
        "miss_report_age_days": miss_stale_days,
        "miss_report": miss_report,
        "verdict": "OK" if not stale_snapshot and not new_dead else "WARN",
    }
    daily_path = os.path.join(REPORTS, "feature_liveness_{0}.json".format(today.replace("-", "")))
    for path in (daily_path, LATEST, prev_path):
        _atomic_write(path, report)

    line = ("[{0}] {1} | total={2} alive={3} sparse={4} constant={5} dead={6} unavailable={7} "
            "new_dead={8} snapshot={9}{10} miss_age={11}d".format(
                report["at"], report["verdict"], len(rows), len(alive), len(sparse),
                len(constant), len(dead), len(unavailable), len(new_dead), snap[:19],
                " STALE" if stale_snapshot else "", "?" if miss_stale_days is None else miss_stale_days))
    with open(os.path.join(REPORTS, "feature_liveness.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        if new_dead:
            fh.write("    새로 죽음: " + ", ".join(new_dead[:20]) + "\n")
    print(line)
    if new_dead:
        print("새로 죽은 피처:", ", ".join(new_dead[:20]))
    if dead:
        print("죽은 피처 예:", ", ".join(dead[:12]))
    return 0 if report["verdict"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
