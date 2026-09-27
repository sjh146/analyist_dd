#!/usr/bin/env python3
"""2026-09-28 틱 정정: U3 est_minutes 실측화 · XR16 측정종결 · CG1(게이트 전이) 신설 · L5b/L5c 각주.

한 번만 쓰는 정정 스크립트(백로그 수정은 이 역할 권한). 실행 후 백업은 .bak 로 남긴다.
"""
import json
import shutil
import sys

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-20260928")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}
changed = []

# ── 1) U3: est_minutes 700 → 1410 (실측 0.38~0.42 페어/초 × 32,576 페어 = 21.6~23.5h) ──
u3 = by["U3"]
old = u3.get("est_minutes")
u3["est_minutes"] = 1410
changed.append(f"U3 est_minutes {old} → 1410")
u3.setdefault("attempts", []).append({
    "ts": "2026-09-28T05:20:00+09:00",
    "rc": 137,
    "verdict": "실행실패(원인 확정: 호스트 정지)",
    "detail": ("호스트 정지 — 빌드 로그 마지막 줄 2026-09-27 19:12:47 KST(15400/32576, 47.3%) "
               "이후 7.6시간 무출력. Windows 이벤트 실측: 이번 부팅 LastBootUpTime 09-28 02:47:19, "
               "절전 이력 09-27 23:54:45→09-28 02:09:55(KST), Kernel-General 정상종료 02:17:14·02:47:10. "
               "Hermes 크론 원장: 09-27 20:00 틱이 'missed its scheduled time(grace 1800s)' 으로 "
               "02:50 에 지연 실행 → 그 사이 틱 자체가 돌지 않았다(호스트 다운). 02:50:46 _record_orphan 이 "
               "유일한 기록. 컨테이너 재생성은 원인 아님(컨테이너 StartedAt 09-27T18:13:52Z = 09-28 03:13 KST > "
               "프로세스 소멸). 체크포인트는 보존됨(panel_995.npz.rows.pkl 23.5MB, 15,000행)."),
    "log": "data/reports/me_cycle/logs/me_cycle_U3_20260927-151127.log",
    "elapsed_min": 699.3,
})
u3["note"] = (u3.get("note", "") +
              " | 2026-09-28 05:2x 정정: est_minutes 700→1410(실측 0.38~0.42 페어/초 기준 21.6~23.5h). "
              "이 값이어야 틱의 ETA 가드가 U3 를 건너뛰어(설계대로) 장중·저녁 창을 넘겨 시작하는 사고를 막고, "
              "착수는 u3_launcher.sh(20:35~21:00)가 --force 로 담당한다. "
              "런처 생존은 이제 틱이 매시간 ensure_launcher() 로 확인해 세션 종료로 죽었으면 setsid 로 재기동한다.")
changed.append("U3 attempts+1 · note 갱신")

# ── 2) XR16: 이미 DB 스크린에서 채점됨 → 종결(신호 없음) ──
xr16 = by.get("XR16")
if xr16:
    xr16["status"] = "done"
    xr16["result"] = {
        "verdict": "신호 없음(측정 종결)",
        "detail": ("_db_feature_screen(2026-09-26) 이 3개 피처를 이미 채점했다 — "
                   "momentum_3_12m 단일AUC 0.4893 · IC t −0.85 (n=62일, 커버리지 21%) / "
                   "relative_strength 0.4986 · t +0.17 (281일) / bb_position 0.5003 · t +1.26 (281일). "
                   "|AUC−0.5| ≤ 0.011 · |IC t| < 1.3 → 단변량 edge 없음. 별도 실험 불필요."),
        "evidence": "data/reports/db_feature_screen_20260926.json",
    }
    changed.append("XR16 backlog → done(측정 종결)")

# ── 3) CG1 신설: 프로덕션 게이트(core48) 전이 검정 ──
cg1 = {
    "id": "CG1",
    "title": "프로덕션 게이트(core48) 전이 검정 — 최고 설정(rank+스무딩)이 게이트를 켜도 유지되는가",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5355,
                 "source": "CO_core30_h5 실측(게이트 ON·5폴드×5시드, HP1 2026-09-26 05:14)"},
    "hypothesis": ("게이트 OFF 경로에서만 양(+)이던 설정이 승격 경로(게이트 ON)에서도 유지된다. "
                   "유지되면 17사이클 만의 승격 후보가 되고, 아니면 스윕 기반 북극성 지표 자체를 재정의해야 한다."),
    "evidence": ("wf_label_sweep.py L341 이 tc.select_curated_features 를 항등으로 몽키패치 → 스윕 AUC 는 "
                 "210피처 풀(게이트 OFF) 값이다. 게이트를 켠 기록값: CO_core30_h5 0.5355±0.0168 · "
                 "CO_core_all_h5 0.5263±0.0246 vs 같은 런 대조군 LS_quant_q30_h5 0.5414 → 게이트가 약 −0.006. "
                 "프로덕션 챔피언 러너는 이 게이트를 쓰므로, 검증 없이 스윕 +방향을 승격 판단에 쓰면 안 된다."),
    "method": ("같은 패널·같은 런 3-arm: LS_quant_q30_h5(게이트 OFF 대조군) · "
               "TR_rank_LBsmooth_h5(게이트 OFF 현 최고 Δ+0.0100) · CO_rank_smooth_h5(게이트 ON, 신설 config). 5폴드×5시드."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 5400 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only LS_quant_q30_h5,TR_rank_LBsmooth_h5,CO_rank_smooth_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": ("CO_rank_smooth_h5 ≥ 0.5555 (게이트 ON 기준선 0.5355 + 사전문턱 0.02) → 승격 후보. "
                "0.5355 초과면 '방향은 전이(효과크기 미달)', 이하면 '비전이 → 북극성 지표 재정의 필요(승인 대상)'"),
    "counterfactual": "CO_core30_h5 (게이트 ON·rank/스무딩 없음) 0.5355 — 같은 조건의 기록값",
    "est_minutes": 60,
    "cost": "75 cell × 약 7~20초 = 유휴 9~25분 · 경쟁 시 최대 1.5h (container timeout 5400s 하드 컷)",
    "metric": "wf_sweep_summary",
    "arm": "CO_rank_smooth_h5",
    "caution": "장중(09:00~15:30)에는 틱 가드가 막는다. timeout 5400s 라 개장 전에 강제 종료된다.",
}
if "CG1" not in by:
    items.append(cg1)
    changed.append("CG1 신설(priority 2, pending)")

# ── 4) L5b/L5c: command 부재로 틱이 영구 건너뛴다 → 각주(소유권 경계) ──
for i in ("L5b", "L5c"):
    it = by.get(i)
    if it:
        it["note"] = (it.get("note", "") +
                      " | 2026-09-28: 이 항목은 command 필드가 없다 → 틱이 'pending 인데 command 가 없다'로 "
                      "영구 건너뛴다(구동기 next_item). 팩터/전략 코드(services/backtester·strategy-agents·job_runner)는 "
                      "이 역할 소유가 아니므로 구현 전 승인이 필요하다.")
        changed.append(f"{i} 각주(소유권 경계)")

b["items"] = items
b["updated_at"] = "2026-09-28T05:25:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("변경:", *changed, sep="\n  ")
print("items:", len(items))
