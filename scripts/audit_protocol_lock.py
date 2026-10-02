#!/usr/bin/env python3
"""계획 락 + 큐 심사 — 실험·릴리스가 '사전 등록된 계약'을 갖고 시작하는가 (읽기 전용).

역할: quant-protocol-lock (docs/QUANT_ROLE_PLAN_V2.md §5.2). 권한: 락 없는 착수는 거부.
실측 근거: `full_pipeline_dd.sh` 가 `--min-improvement 0.0` 으로 돌면서 사전등록 문턱은 +0.02 였고,
그 불일치가 노이즈급 승격(MT116)을 통과시켰다. 또 핸드오프는 생성 18 / 미소비 15 로 큐가 쌓이기만 했다.

출력: reports/locks/lock_audit_<YYYY-MM-DD>.json · 위반만 stdout.
종료코드: 0 정상 / 2 위반.
"""
import datetime as dt
import glob
import json
import os
import re
import sys

REPO = "/home/jhshi/analyist_dd"
OUT_DIR = os.path.join(REPO, "reports", "locks")
BACKLOGS = ["docs/QUANT_MODEL_BACKLOG.json", "docs/QUANT_RESEARCH_BACKLOG.json",
            "docs/QUANT_TRADER_BACKLOG.json"]
REQUIRED = ("counterfactual", "success", "est_minutes", "command")
MIN_IMPROVEMENT_FLOOR = 0.02   # 엔지니어 사전등록 문턱(폴드 std ±0.03 → +0.02 이상만 신호)


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def items(path):
    d = load(os.path.join(REPO, path), {})
    if not isinstance(d, dict):
        return []
    return d.get("items") or []


def main():
    now = dt.datetime.now()
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = {"ts": now.isoformat(timespec="seconds"), "mode": "protocol_lock",
           "violations": [], "queue": {}, "info": {}}

    # 1) pending 항목의 계약 필드 (needs_setup 은 '준비 필요' 버킷이므로 위반이 아니다 — 개수만 info 로)
    checked = 0
    needs_setup = 0
    for bf in BACKLOGS:
        for it in items(bf):
            st_ = (it.get("status") or "")
            if st_ == "needs_setup":
                needs_setup += 1
                continue
            if st_ != "pending":
                continue
            checked += 1
            missing = [k for k in REQUIRED if not it.get(k)]
            if missing:
                rec["violations"].append({"check": "lock_fields_missing", "backlog": bf,
                                          "id": it.get("id"), "detail": f"누락 필드: {missing}"})
            # est_minutes vs 컨테이너 timeout
            cmd = str(it.get("command") or "")
            m = re.search(r"timeout\s+(\d+)", cmd)
            est = it.get("est_minutes")
            if m and isinstance(est, (int, float)) and float(est) * 60 > float(m.group(1)):
                rec["violations"].append({
                    "check": "est_exceeds_timeout", "backlog": bf, "id": it.get("id"),
                    "detail": f"est {est}분 = {est*60:.0f}s > timeout {m.group(1)}s — 완주 전에 잘린다"})
    rec["info"]["items_checked"] = checked
    rec["info"]["needs_setup"] = needs_setup

    # 2) 승격 문턱 = 사전등록 값인가 (주석 줄은 제외 — 주석에 적힌 옛 값이 오탐을 만든다)
    sh = os.path.join(REPO, "scripts", "full_pipeline_dd.sh")
    if os.path.exists(sh):
        code_lines = [l for l in open(sh, encoding="utf-8", errors="replace").read().splitlines()
                      if not l.strip().startswith("#")]
        src = "\n".join(code_lines)
    else:
        src = ""
    vals = re.findall(r"--min-improvement\s+([0-9.]+)", src)
    rec["info"]["pipeline_min_improvement"] = vals
    for v in vals:
        if float(v) < MIN_IMPROVEMENT_FLOOR:
            rec["violations"].append({
                "check": "threshold_below_registered_floor",
                "detail": f"full_pipeline_dd.sh --min-improvement {v} < 사전등록 {MIN_IMPROVEMENT_FLOOR} "
                          f"(노이즈급 승격 통과 — MT116 원인)"})

    # 3) 큐 심사(H): 핸드오프 미소비
    led = os.path.join(REPO, "data", "reports", "trader_ledger.jsonl")
    if os.path.exists(led):
        rows = [json.loads(l) for l in open(led, encoding="utf-8", errors="replace") if l.strip()]
        last = rows[-1] if rows else {}
        total, unfilled = last.get("handoffs_total"), last.get("handoffs_unfilled")
        rec["queue"] = {"handoffs_total": total, "handoffs_unfilled": unfilled, "ts": last.get("ts")}
        if isinstance(unfilled, int) and unfilled > 0:
            rec["violations"].append({
                "check": "handoffs_unconsumed",
                "detail": f"핸드오프 미소비 {unfilled}/{total} (기준 {rec['queue']['ts']}) — "
                          f"소비 계획을 역할에 배정해야 한다"})

    # 4) 락 파일 현황
    locks = sorted(os.path.basename(p) for p in glob.glob(os.path.join(REPO, "docs", "locks", "*.lock.json")))
    rec["info"]["locks"] = locks

    # 5) 판정 불가 항목은 정직하게 표기
    rec["info"]["unverified"] = [
        "표본 유니버스 재현성(4회 교집합 100%) — 컨테이너 필요: "
        "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_universe_determinism_test.py",
    ]

    rec["status"] = "violations" if rec["violations"] else "ok"
    path = os.path.join(OUT_DIR, f"lock_audit_{now.date().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if rec["violations"]:
        print(f"[audit-lock] 위반 {len(rec['violations'])}건 · {path}")
        for v in rec["violations"]:
            print(f"  - {v['check']}: {v.get('id') or ''} {v['detail']}")
    return 2 if rec["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
