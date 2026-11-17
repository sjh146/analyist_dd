#!/usr/bin/env python3
"""CG27 등록 + CG26 결과에 대한 결론 기록 + CG26 원장 '보고됨' 표시 (2026-09-29 06:1x).

[CG26 실측 — 12.5분, panel_420_asofpatch(49종목)·게이트 ON·5폴드×5시드]
  CO_core30_h5 0.5363±0.0188  [0.5241, 0.5078, 0.5614, 0.5491, 0.5389]
  CO_core15_h5 0.5296±0.0170  Δ−0.0067
  CO_core20_h5 0.5337±0.0194  Δ−0.0026
  CO_core40_h5 0.5318±0.0131  Δ−0.0045
→ k=30 이 최적, 더 작아도(core15/20) 더 커도(core40/48=0.5263) 나쁘다.
  등록 문턱 +0.02 미달 폭이 −0.0026~−0.0067 이므로 '게이트 ON 선별 크기' 축은 종료.

[CG27 등록 — 왜 이 축인가]
게이트 ON(승격 경로)에서 ≥+0.02 를 낸 유일한 arm 은 CG8 의 CO_rank_smooth_d1_h5
0.5592±0.0075 인데 **rank 변환**이 들어 있다 → 추론이 종목 단위 스트리밍
(app/inference/predictor.py L86-89)이라 날짜별 횡단면 rank 를 만들 수 없어 배포 불가.
그런데 같은 조합의 나머지 두 성분(라벨 스무딩 kind=smooth · depth1·lr0.05 recipe)은
**학습 옵션일 뿐 추론 계약을 건드리지 않는다** → '스무딩+depth1'(rank 없음)이 얼마를
남기는지가 미측정으로 남아 있었다. 그걸 생산 유니버스(49종목)에서 10시드로 잰다.
"""
import json
import shutil

import model_engineer_cycle as m

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg27")
b = json.load(open(P, encoding="utf-8"))
by = {i["id"]: i for i in b["items"]}

cg27 = {
    "id": "CG27",
    "title": "배포 가능한 게이트 ON 최고 조합 — 라벨 스무딩+depth1(rank 없음)을 생산 유니버스에서 10시드 재측정",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5363,
                 "source": "CG26 같은 런 게이트 ON 대조군 CO_core30_h5 (panel_420_asofpatch·5폴드×5시드)"},
    "arm": "CO_smooth_d1_h5",
    "counterfactual": "CO_core30_h5 (같은 런 게이트 ON 대조군)",
    "hypothesis": ("rank 없는 배포 가능 조합(라벨 스무딩 + depth1·lr0.05)이 같은 런 대조군 대비 +0.02 이상을 낸다. "
                   "지금까지 게이트 ON 에서 ≥+0.02 를 낸 유일한 arm 은 rank 를 포함해 배포 불가였고, "
                   "그 조합에서 '배포 가능한 부분'만 남긴 값을 아무도 재지 않았다."),
    "evidence": ("CG8(panel_420_asofpatch·5폴드×10시드): CO_core30_h5 0.5365 · CO_d1_h5 0.5545±0.0458(Δ+0.0180) · "
                 "CO_rank_smooth_d1_h5 0.5592±0.0075(Δ+0.0227, rank 포함=배포 불가). "
                 "CG11(panel_150u): CO_smooth_h5 150종목에서 대조군 0.5101 대비 −0.0022 → 스무딩 단독은 평균을 거의 안 올린다. "
                 "CG26(2026-09-29 06:13, 12.5분): core30 0.5363±0.0188 · core15 0.5296 · core20 0.5337 · core40 0.5318 "
                 "→ 선별 크기 축 종료(k=30 최적). 49종목 패널에서 CO_smooth_h5 는 아직 미측정이다."),
    "method": ("같은 런 4-arm · 같은 패널·같은 행·같은 유니버스(49종목) → 유니버스 교체 잡음(CG13 ±0.0287)이 섞이지 않는다: "
               "CO_core30_h5(대조군) · CO_smooth_h5(스무딩 단독) · CO_smooth_d1_h5(배포 후보) · CO_d1_h5(HP 단독). "
               "5폴드×10시드. 판정 = arm mean − 대조군 mean ≥ +0.02 **이고** 폴드 짝 부호 ≥4/5."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 5400 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 10 "
                "--only CO_core30_h5,CO_smooth_h5,CO_smooth_d1_h5,CO_d1_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": ("CO_smooth_d1_h5 − CO_core30_h5 ≥ +0.02 (폴드 짝 부호 ≥4/5) → 승격 검토 대상(추론 계약 무변경: "
                "라벨·HP 는 학습 옵션이라 배포 가능). 미달이면 조정 축(피처변환·HP·유니버스·라벨·가중·선별크기) "
                "전체를 '게이트 ON 천장 < +0.02' 로 닫고 다음 레버를 데이터 축(L3·부활 피처)으로 확정한다."),
    "expected": "미지 — d1 단독 +0.0180(CG8)·스무딩은 분산 축소 성분이라 평균 기여가 작다 → 0.550~0.560 구간 추정.",
    "est_minutes": 25,
    "cost": "4 config × 5폴드 × 10시드 = 200 cell (CG26 실측 7.5s/cell → 약 25분, timeout 5400s)",
    "metric": "wf_sweep_summary",
    "caution": ("개장 09:00 전 종료 필요: 07:00 시작 시 07:25 종료. 평일 20:00 컨테이너 재생성 창과 겹치지 않는다. "
                "CO_smooth_d1_h5 는 이번에 wf_label_sweep.py CONFIGS 에 신설했고(스모크 2폴드×1시드 완주 0.5386) "
                "컨테이너는 /app/scripts 바인드 마운트라 즉시 반영된다. wf_label_sweep.py 는 code_sig(feature_engine/**) "
                "대상이 아니라 U3 패널 빌드에 영향이 없다."),
    "note": ("2026-09-29 06:1x 신설 — CG26(선별 크기) 이 노이즈로 닫힌 직후. 남은 pending 은 U3(장시간·런처 20:35 담당)뿐이라 "
             "밤 남은 시간에 돌릴 게이트 ON 저비용 축으로 등록. 이 항목이 +0.02 를 내면 승격 후보 생성(CG9 A단계)으로 이어진다."),
}
if "CG27" not in by:
    b["items"].append(cg27)

cg26 = by["CG26"]
cg26["note"] = (cg26.get("note", "") +
                " | [결론 2026-09-29 06:13] 4-arm 실측(12.5분): core30 0.5363±0.0188 이 최고, "
                "core15 0.5296(Δ−0.0067) · core20 0.5337(Δ−0.0026) · core40 0.5318(Δ−0.0045). "
                "선별 크기를 낮추면 오히려 나빠지고 키워도(core40·core48 0.5263) 나빠진다 → "
                "**게이트 ON 선별 크기 축 종료(k=30 이 최적)**. 남은 후속은 CG27(배포 가능 조합 분해).")

b["updated_at"] = "2026-09-29T06:20:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ── 원장: CG26 을 '보고됨'으로 표시(이 세션에서 사용자에게 보고했다) ───────────
led = m.load_ledger()
n = 0
for r in led:
    if r.get("id") == "CG26" and not r.get("reported"):
        r["reported"] = True
        n += 1
m._rewrite_ledger(led)

print("CG27 등록 · CG26 note 갱신 · 원장 reported 표시", n, "건 · items:", len(b["items"]))
