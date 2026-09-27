#!/usr/bin/env python3
"""_patch_backlog_20260928 — 백로그 정리 3건 (엔지니어, 2026-09-28 04:1x).

① TR3(rank 재현성 확정) 종료: 두 번의 실측(TR1 3시드 Δ+0.0107 / TR2 5시드 Δ+0.0081)으로
   효과크기 상한이 문턱(+0.02) 미달임이 드러났다 → 시드 10 재측정(4시간)은 같은 축을
   다시 재는 것이라 큐에서 제거한다(축 종료). 미측정이므로 'done' 으로 쓰지 않는다.
② U3b → needs_setup: panel_995.npz 가 아직 없어(command 가 즉시 실패) 시도 횟수만 태운다.
③ LB1 신설: 라벨 잡음 축(스무딩·변동성조정) — 다른 축이 15사이클 전부 노이즈였으므로 다음 레버.
"""
import json
import os
from datetime import datetime

PROJ = "/home/jhshi/analyist_dd"
PATH = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")

b = json.load(open(PATH, encoding="utf-8"))
items = {i["id"]: i for i in b["items"]}
now = datetime.now().astimezone().replace(microsecond=0).isoformat()

# ① TR3 종료
tr3 = items["TR3"]
tr3["status"] = "failed"
tr3["closed_at"] = now
tr3["closed_by"] = "engineer"
tr3["closed_reason"] = (
    "축 종료(미측정·재측정 중단 판단). 실측 효과크기 상한: TR1 5폴드×3시드 Δ+0.0107 "
    "(0.5519 vs 0.5412, 폴드 3/5 승) · TR2 5폴드×5시드 Δ+0.0081 (0.5495 vs 0.5414) — "
    "둘 다 문턱 +0.02 의 절반 이하이고 시드를 늘려도 추정치가 문턱에 도달할 근거가 없다. "
    "시드 10 재측정(4시간·경쟁 시 6시간+)은 같은 축의 정밀도만 올린다 → 다른 축(LB1 라벨 잡음)으로 "
    "자원을 옮긴다. 수정 전 구동기 결함으로 이 항목이 priority=1 급 항목만 계속 재시도되는 "
    "구조였던 것도 함께 정리(scripts/model_engineer_cycle.py next_item ETA 스킵)."
)
tr3["result"] = {
    "verdict": "노이즈(축 종료)",
    "detail": "재측정 미실행 — 기존 2회 실측(Δ+0.0107 3시드, Δ+0.0081 5시드)으로 "
              "효과크기 상한 < +0.02 판단. rank 변환은 '노이즈' 축으로 닫는다.",
    "delta": None, "per_exp": None, "rc": None,
}

# ② U3b 는 panel_995 완성 후에만 실행 가능
u3b = items["U3b"]
u3b["status"] = "needs_setup"
u3b["setup_needed"] = ("panel_995.npz 부재(U3 패널 빌드가 47.3%에서 두 번 소실) — "
                       "U3 완주 후 panel_subset.py 로 w281 부분집합을 만들 수 있다.")
u3b["note"] = (u3b.get("note", "") +
               " [2026-09-28] pending→needs_setup: panel_995.npz 가 없어 command 가 즉시 "
               "실패하고 재시도 카운터만 소모한다.")

# ③ LB1 신설 (라벨 잡음 축)
if "LB1" not in items:
    b["items"].append({
        "id": "LB1",
        "title": "라벨 잡음 축: 선행 1~5일 평균(스무딩) · 변동성 조정 라벨 — 같은 런 A/B",
        "status": "pending",
        "priority": 2,
        "affects_model": True,
        "baseline": {"value": 0.5406,
                     "source": "panel_420_asofpatch · 5폴드 확장창 기록 기준선"},
        "hypothesis": ("피처·HP·유니버스 축이 15사이클 전부 노이즈(Δ<+0.02)였으므로 남은 레버는 "
                       "라벨 쪽이다. 5일 보유와 불일치하는 '점대점 5일 방향' 라벨의 잡음을 줄이면"
                       "(① 선행 1~5일 수익률 평균 ② 수익률÷후행 20일 변동성) 같은 피처로도 AUC 가 오른다."),
        "evidence": ("① 병목은 피처 정보량: 단일피처 최고 AUC 0.5072 · |IC| 최고 0.0202 "
                     "(_db_feature_screen, 38개 중 31개 채점). ② 라벨 축 전례: 같은 피처셋에서 라벨만 "
                     "바꿔 0.5133 → 0.5270 → 분위+호라이즌(+0.06). ③ 기존 라벨 변형은 q(0.30/0.25/0.20)·"
                     "호라이즌(h3/h5/h8/h10)·시장상대(중앙값) 뿐 — 라벨 잡음 구조는 미측정."),
        "method": ("같은 패널·같은 런에서 3 arm(대조군 LS_quant_q30_h5 / LB_smooth_q30_h5 / "
                   "LB_voladj_q30_h5) 5폴드×5시드. 라벨 kind(smooth·voladj)는 wf_wave.make_labels 에 "
                   "구현 — 모두 선행 shift(-k)·후행 rolling 만 사용(시점정합, 미래정보 없음)."),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 7200 "
                    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                    "--days 420 --limit 50 --folds 5 --seeds 5 --only "
                    "LS_quant_q30_h5,LB_smooth_q30_h5,LB_voladj_q30_h5'"),
        "check": "python3 scripts/model_engineer_cycle.py --status",
        "check_target": {"op": ">=", "value": 1},
        "metric": "wf_sweep_summary",
        "arm": "LB_smooth_q30_h5",
        "counterfactual": "LS_quant_q30_h5 (같은 런·같은 패널 대조군)",
        "cost": "유휴 약 5분 / 부하 경쟁 시 최대 2시간(timeout 7200s = 08:30 이전 종료 상한)",
        "est_minutes": 120,
        "success": ("LB_smooth_h5 또는 LB_voladj_h5 의 폴드 평균 AUC 가 같은 런 대조군 대비 "
                    "+0.02 이상(폴드승률 ≥0.8). 미달이면 라벨 잡음 축도 노이즈로 닫는다."),
        "note": ("judge_per 는 arm 하나만 비교한다 → 원장 per_exp 에 3 arm 이 모두 있으므로 "
                 "다음 틱에서 LB_voladj 도 수동 재판정하라(둘 중 큰 쪽이 신호면 그쪽을 승격 대상으로)."),
    })

b["updated_at"] = now
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)

print("updated_at:", now)
for i in sorted(b["items"], key=lambda x: (x.get("priority", 99), x["id"])):
    if i["id"] in ("TR3", "U3b", "LB1", "U3"):
        print(f"  [{i['status']:11s}] {i['id']}")
