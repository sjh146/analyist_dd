#!/usr/bin/env python3
"""U3 사망 백필 + 백로그 갱신(est_minutes 실측·TR3 신규) — 1회성 정정 스크립트."""
import json
import sys

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m  # noqa: E402

rec = {
    "ts": "2026-09-26T19:55:00+09:00",
    "id": "U3",
    "title": "확장 이력 패널(995일 창 = 659거래일) 재빌드 + 로버스트 재측정",
    "rc": 137,
    "elapsed_min": 57.5,
    "log": "data/reports/me_cycle/logs/me_cycle_U3_20260926-185733.log",
    "metric": None,
    "parsed": {
        "error": "실행 실패 — 측정값 없음",
        "rc": 137,
        "cause": ("외부 종료(사용자 컴퓨터 종료 준비가 의도적으로 정지 — scripts/train_departure_prep.sh 가 "
                  "pid 14876 + wf_label_sweep 14887/14888/14908/14914/14915 정지). "
                  "구동기가 원장 기록 전에 함께 SIGKILL 되어 기록이 없었음"),
        "progress": "빌드 1,400/32,626 (4.3%) · 0.42 pair/s · 체크포인트 1,000행 보존",
    },
    "verdict": "실행실패",
    "detail": ("외부 종료(사용자 종료 준비가 의도적으로 정지) — 측정값 없음 · "
               "빌드 진행 1,400/32,626 (4.3%) · 체크포인트 1,000행 보존(재개 가능)"),
    "reported": True,
}
led = [json.loads(l) for l in open(m.LEDGER, encoding="utf-8") if l.strip()]
assert not any(r.get("id") == "U3" for r in led), "이미 U3 기록이 있다 — 중복 방지"
m.append_ledger(rec)
print("[ledger] U3 실행실패 기록 추가 OK · 총", len(led) + 1, "건")

b = m.load_backlog()
for it in b["items"]:
    if it["id"] == "U3":
        it.setdefault("attempts", []).append({
            "ts": rec["ts"], "rc": 137, "verdict": "실행실패", "detail": rec["detail"],
            "log": rec["log"], "elapsed_min": rec["elapsed_min"]})
        it["status"] = "pending"
        it["retry_note"] = ("2026-09-26 19:55 기록 없이 종료(사용자 종료 준비) → 재시도 1/3 "
                            "(체크포인트 재개)")
        it["result"] = {"verdict": "실행실패", "detail": rec["detail"], "delta": None,
                        "per_exp": None, "rc": 137}
        it["est_minutes"] = 1250
        it["caution"] = (it.get("caution", "") +
                         " | 2026-09-26 실측: 부하(load1 7.2·postgres 경쟁) 하에서 0.41 페어/초 → "
                         "32,626페어 ETA 20.9h = est_minutes 700(1.2~2.7페어/초 가정)의 3배. "
                         "19:55 사용자 종료 준비로 1,400/32,626(4.3%)에서 SIGKILL. "
                         "체크포인트 500페어 단위·재개 가능 → 야간 중단 손실은 최대 500페어. "
                         "21h 작업이라 평일 ETA 가드(20:00 재생성 창)에 걸려 주말에만 시작된다.")
        print("[backlog] U3 갱신:", it["status"], "est", it["est_minutes"],
              "attempts", len(it["attempts"]))

if "TR3" not in {i["id"] for i in b["items"]}:
    b["items"].append({
        "id": "TR3",
        "title": "rank 변환 재현성 확정: TR_rank_h5 5폴드×10시드 vs 같은 런 대조군 LS_quant_q30_h5",
        "status": "pending",
        "priority": 1,
        "affects_model": True,
        "baseline": {"value": 0.5412,
                     "source": "TR1 동일 런 대조군 LS_quant_q30_h5(5폴드×3시드) · panel_420_asofpatch"},
        "hypothesis": ("지금까지 유일하게 부호가 재현된 방향(횡단면 rank 변환)이 시드 노이즈인지 "
                       "작은 실효인지 확정한다. TR1 Δ+0.0107 · TR2 Δ+0.0081, 두 번 모두 폴드승률 1.0."),
        "evidence": ("TR1 0.5519 vs 0.5412(Δ+0.0107, 폴드 5/5 승) · TR2 0.5495 vs 0.5414(Δ+0.0081, 5/5). "
                     "둘 다 +0.02 문턱 미달로 '노이즈' 기록이지만 방향·승률이 반복됐다 — 시드 3→10 으로 "
                     "평균의 시드 노이즈를 줄여 부호검정을 강화한다."),
        "method": ("같은 패널·같은 런에서 대조군과 실험군을 함께 측정(교차패널 비교 금지). "
                   "시드 10개로 각 폴드 평균의 표준오차를 약 1.8배 줄인다."),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                    "--days 420 --limit 50 --folds 5 --seeds 10 "
                    "--only LS_quant_q30_h5,TR_rank_h5'"),
        "check": "python3 scripts/model_engineer_cycle.py --status",
        "check_target": {"op": ">=", "value": 1},
        "cost": "약 15~25분(시드 3→10, config 2개)",
        "success": "TR_rank_h5 평균 − LS_quant_q30_h5 평균 ≥ +0.02 (부호 일관 + 폴드승률 ≥ 0.8)",
        "counterfactual": "LS_quant_q30_h5 (같은 런 대조군)",
        "arm": "TR_rank_h5",
        "metric": "wf_sweep_summary",
        "est_minutes": 30,
        "note": ("부호가 재현된 유일한 축이라 '노이즈'로 닫기 전에 시드 노이즈를 확정한다. 통과하면 다음 "
                 "실험 대조군을 rank 변환판으로 승격하고, 미달이면 이 축도 닫는다."),
    })
    print("[backlog] TR3 추가(pending, est 30분)")
b["updated_at"] = m.now_kst().isoformat(timespec="seconds")
m.save_backlog(b)

b2 = m.load_backlog()
u3 = [i for i in b2["items"] if i["id"] == "U3"][0]
print("검증 U3:", u3["status"], "est", u3["est_minutes"], "attempts", len(u3["attempts"]),
      "| retry:", u3.get("retry_note"))
print("검증 TR3:", [(i["id"], i["status"]) for i in b2["items"] if i["id"] == "TR3"])
print("pending:", sorted(i["id"] for i in b2["items"] if i["status"] == "pending"))
