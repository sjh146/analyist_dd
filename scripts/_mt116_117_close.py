#!/usr/bin/env python3
"""MT116/MT117 을 수리 완료로 닫고, 열린 핸드오프 목록을 출력한다 (일회성, 큐 심사 H).

- MT116(챔피언 교체가 swing 경로를 닫음): 롤백 + 승격 게이트 + 라이브 스코어 사전검사로 종결.
- MT117(교체/롤백 미기록): scripts/log_champion_swap.py + 파이프라인 배선으로 종결.
쓰기는 이 두 항목에만 한다(다른 항목·역할 파일 불변).
"""
import datetime as dt
import json
import os

REPO = "/home/jhshi/analyist_dd"
MODEL = os.path.join(REPO, "docs", "QUANT_MODEL_BACKLOG.json")
TODAY = dt.date.today().isoformat()

RESOLUTIONS = {
    "MT116": {
        "status": "done",
        "result": {
            "verdict": "수리 완료(롤백 + 게이트 + 사전검사)",
            "measured": "같은 유니버스(KOSDAQ 300)·같은 피처행렬 A/B: 교체된 챔피언 0.55 초과 0건(max 0.4733) "
                        "vs 직전 챔피언 9건(max 0.6733). 10-01 은 상승일(68.1% 상승)이라 시장 탓이 아님.",
            "actions": [
                "롤백: champion_promote --candidate app/models/champion_prev_20261001-122646 --min-improvement -0.01 "
                "→ champion auc 0.554776 → 0.551318 (백업 champion_prev_20261002-024420)",
                "게이트: full_pipeline_dd.sh --min-improvement 0.0 → 0.02 (노이즈급 승격 차단, dry-run A/B 검증)",
                "가드: tools/release_precheck.py --target champion --candidate <dir> --with-probe "
                "(후보의 0.55 초과 신호 0건이면 BLOCK) + scripts/_swing_ensemble_weight_probe.py PROBE_MODEL_DIRS",
                "감사: scripts/audit_measure.py 가 매일 batch_type·서브모델 AUC 격차를 점검(15:50 크론)",
            ],
            "residual": "swing 피드 복구는 다음 08:30 파이프라인 결과로 확인해야 한다(미검증).",
            "closed_by": "orchestrator(사용자 지시로 자율 진행)",
            "closed_at": TODAY,
        },
    },
    "MT117": {
        "status": "done",
        "result": {
            "verdict": "수리 완료(교체·롤백 자동 기록)",
            "actions": [
                "scripts/log_champion_swap.py 신설: champion auc·prev 디렉터리·prev auc·robust_auc 메타를 "
                "data/reports/champion_swaps.jsonl 에 멱등 append",
                "scripts/full_pipeline_dd.sh 승격 단계 뒤에 호출 배선(실패해도 파이프라인 계속: `|| true`)",
                "실측: 2026-10-02 롤백 1줄 기록 확인(auc=0.551318 · prev=champion_prev_20261002-024420(0.554776))",
            ],
            "closed_by": "orchestrator(사용자 지시로 자율 진행)",
            "closed_at": TODAY,
        },
    },
}


def main():
    d = json.load(open(MODEL, encoding="utf-8"))
    closed = []
    for it in d.get("items", []):
        r = RESOLUTIONS.get(str(it.get("id")))
        if not r:
            continue
        it["status"] = r["status"]
        it["result"] = r["result"]
        it["closed_at"] = TODAY
        it["note"] = (it.get("note") or "") + \
            f"\n[종결 {TODAY}] {r['result']['verdict']} — 근거·명령은 result 참조."
        closed.append(it["id"])
    with open(MODEL, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print("종결:", closed)

    # 열린 핸드오프 목록(다른 백로그 포함)
    for path in ("docs/QUANT_MODEL_BACKLOG.json", "docs/QUANT_RESEARCH_BACKLOG.json",
                 "docs/QUANT_TRADER_BACKLOG.json"):
        p = os.path.join(REPO, path)
        if not os.path.exists(p):
            continue
        b = json.load(open(p, encoding="utf-8"))
        for i in b.get("items", []):
            if not (i.get("from_trader") or i.get("from_research")):
                continue
            if i.get("command") or i.get("status") in ("done", "closed_rejected"):
                continue
            print(f"  OPEN {path.split('.')[0].split('_')[-1]:8s} {i.get('id'):6s} "
                  f"status={i.get('status'):12s} prio={i.get('priority')} :: {str(i.get('title'))[:80]}")


if __name__ == "__main__":
    main()
