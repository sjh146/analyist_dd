#!/usr/bin/env python3
"""CG26 등록(2026-09-29 05:4x) — 게이트 ON 경로의 '선별 크기' 최적점.

배경(CG23·CG25 판정 직후): 표본 가중 축(Δ-0.0003)과 피처 시간변화율 파생 축
(Δ+0.0031, 5/5 부호 일치)이 모두 사전문턱 +0.02 에 못 미쳐 닫혔다. 남은 pending 은
U3(장시간 패널 빌드)뿐이고 그것은 런처(20:35)가 담당한다 → 밤사이 남은 시간에 돌릴
**저비용·게이트 ON(승격 경로)** 실험으로 CG26 을 등록한다.

왜 이 축인가: 게이트 ON 에서 선별 크기 곡선은 점이 둘뿐이다 —
  core30 0.5355±0.0168 / core48 전체 0.5263±0.0246  (CG1·CG2, 같은 런)
즉 48 은 30 보다 나쁘다. 반면 게이트 OFF 곡선(SEL1)은 top10 0.5364 · top15 0.5343 ·
top20 0.5345 · top30 0.5414 로 30 까지 상승한다 → **게이트 ON 최적점이 30 보다 작은지**
가 미측정으로 남아 있었다. 양(+)이면 k 를 낮추는 것만으로 승격 경로가 개선된다
(추론 계약 변경 없음 — 순수 config 축이라 승인 불필요).
"""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg26")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}

cg26 = {
    "id": "CG26",
    "title": "게이트 ON 경로의 선별 크기 최적점 — core15/20/40 vs core30 (같은 런·폴드 짝)",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5406,
                 "source": "등록 기준선 LS_quant_q30_h5 (게이트 OFF·49종목·281거래일)"},
    "hypothesis": ("게이트 ON(core48) 경로에서 edge 선별 크기 k=30 은 최적이 아니다. 게이트 ON 곡선은 "
                   "k=30(0.5355)·k=48(0.5263) 두 점뿐이고 48 이 더 나쁘다 → 최적점이 30 보다 작을 수 있다."),
    "evidence": ("CG1·CG2 같은 런 실측(panel_420_asofpatch·5폴드×5시드): CO_core30_h5 0.5355±0.0168 · "
                 "CO_core_all_h5 0.5263±0.0246 · 게이트 OFF 대조군 LS_quant_q30_h5 0.5414. "
                 "게이트 OFF 는 SEL1 에서 top10 0.5364 · top15 0.5343 · top20 0.5345 · top30 0.5414 로 "
                 "30 까지 상승 — 두 곡선의 모양이 다르므로 게이트 ON 최적점은 별도 측정이 필요하다. "
                 "스모크(2026-09-29 05:5x, 2폴드×1시드, 판정 불가): top15 0.5275 > top40 0.5206 > top20 0.5170."),
    "method": ("같은 패널·같은 런 4-arm: CO_core30_h5(대조군) · CO_core15_h5 · CO_core20_h5 · CO_core40_h5, "
               "5폴드×5시드. 같은 행·같은 유니버스에서 선별 k 만 다르므로 유니버스 교체 잡음(CG13: ±0.0287)이 "
               "섞이지 않는다. 판정 = arm(최고) − 대조군 ≥ +0.02 이고 폴드 승률 ≥ 0.8."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only CO_core30_h5,CO_core15_h5,CO_core20_h5,CO_core40_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "counterfactual": "CO_core30_h5 (같은 런 게이트 ON 대조군)",
    "success": ("최고 arm 이 같은 런 대조군 CO_core30_h5 대비 +0.02 이상 + 폴드 승률 ≥0.8 → "
                "'게이트 ON 최적 k' 확정 후 10시드·panel_150u 재현 확인(3-arm 중 최고값 선택 편향 방지). "
                "미달이면 '게이트 ON 선별 크기 축 종료'로 닫는다."),
    "expected": "미지 — 게이트 OFF 곡선은 30 에서 최고였고 게이트 ON 은 48 이 30 보다 나쁘다. 두 정보가 상충하므로 실측만이 답이다.",
    "est_minutes": 10,
    "cost": "4 config × 5폴드 × 5시드 = 100 cell (CG1: 75 cell 2.1분 → 유휴 3~5분, 경쟁 시 상한 3600s)",
    "metric": "wf_sweep_summary",
    "caution": ("장중 가드는 틱이 막는다(장외 전용). 스모크는 /tmp 로 분리 실행했고 이 항목은 기본 요약 경로를 쓴다. "
                "U3 패널 빌드(런처 20:35 시작)와 겹치면 CPU 를 나눠 갖는다 — 밤 20:00 이후로 밀리면 그때는 런처가 우선이다."),
    "note": ("2026-09-29 05:4x 신설 — CG23(표본 가중)·CG25(Δ파생) 판정 직후. 두 축이 모두 사전문턱 미달로 닫혀 "
             "남은 pending 이 U3(장시간·런처 담당)뿐이었으므로, 남은 밤 시간에 돌릴 게이트 ON 저비용 축으로 등록했다. "
             "wf_label_sweep.py CONFIGS 에 CO_core15_h5 · CO_core20_h5 · CO_core40_h5 추가(컨테이너는 /app/scripts 바인드 마운트라 "
             "이미 반영). 스모크 3종 정상 완주 확인(2폴드×1시드)."),
}
if "CG26" not in by:
    items.append(cg26)

# ── U3: 오늘 새벽 실패 사실과 재실행 계획을 기록(코드 변경 아님, 판단 근거만 갱신) ──
u3 = by["U3"]
u3["caution"] = (
    u3.get("caution", "")
    + " | 2026-09-29 02:29 실측: 전일 20:35 재개(46%) 빌드가 32,576/32,576(100%) 완주 후 **저장 거부** — "
      "01:48 KST 커밋 c4b431c 가 feature_engine 을 수정해 code_sig 1790334858.601 → 1790614188.127 로 바뀌었다"
      "(RuntimeError, wf_wave L216 종료 검사). 체크포인트 파일도 함께 삭제돼 오늘 밤은 **0% 부터** 재빌드다."
      " 실측 속도 0.82 pair/s → 전량 32,576 페어 = 약 663분(11.0h). 20:35 시작 시 07:38 종료 예상"
      "(컨테이너 timeout 43200s = 12h, 개장 09:00 전; 런처 종료 상한 08:55)."
      " 재발 방지: feature_pipeline._assert_code_unchanged 가 **종목 배치마다** 호출되도록 패치(09-29 03:0x, 컨테이너 반영 확인) "
      "— 앞으로는 저장 시점이 아니라 코드 변경 직후 중단된다(48/162 페어 조기 중단 e2e 실측).")
u3["command"] = u3["command"].replace("timeout 42000", "timeout 43200")
u3["cost"] = ("전량 재빌드 32,576 페어 ÷ 0.82 pair/s = 663분(11.0h) → 20:35 시작 시 07:38 종료. "
              "timeout 43200s(12h) = 08:35 상한, 개장 09:00 전. 체크포인트 500페어마다 저장 → 중단 시 재개.")

b["updated_at"] = "2026-09-29T05:50:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG26 신설 · U3 command timeout 43200 · items:", len(items))
print(json.dumps(cg26["command"], ensure_ascii=False))
print(json.dumps(u3["command"], ensure_ascii=False))
