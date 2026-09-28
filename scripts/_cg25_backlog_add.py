#!/usr/bin/env python3
"""_cg25_backlog_add.py — CG25(피처 시간 변화율 Δ 파생 축) 를 백로그에 pending 으로 추가한다.

배경(2026-09-29 04:3x):
  · CG23(표본 가중) 판정 = 노이즈: 구간 짝 Δ 평균 −0.0003 (2/5 양) → 시간감쇠 가중 축 닫음.
  · 실행 가능한 pending 이 다시 없어, 남은 미시험 축 중 **피처 시간 변화율(Δ)** 을 구현했다.
  · 구현 검증: scripts/_derived_feature_test.py 14/14 PASS (npz 원본에서 독립 계산한 Δ 와
    비트 일치 · 첫 k행 NaN = 미래 참조 없음) + 스모크(DDn/DDd_00_30, 2폴드×1시드)에서
    arm 이 'edge top30 + Δ파생 12개 = 42피처' 로 실제 학습됨(선별 경쟁 제거 설계).

사용: python3 scripts/_cg25_backlog_add.py <BACKLOG_JSON>
"""
import json
import sys

SLICES = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150)]
IDS = []
for a, b in SLICES:
    IDS += [f"DDn_{a:02d}_{b}", f"DDd_{a:02d}_{b}"]

CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
       "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
       "--folds 5 --seeds 5 --only " + ",".join(IDS) + "'")

ITEM = {
    "id": "CG25",
    "title": "피처 시간 변화율(Δ1·Δ5) 파생 축 — 사전 등록 6소스 · 구간 짝 5쌍 (기록상 0회 시험)",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5101,
                 "source": "CG23 같은 런 게이트 ON 대조군 CO_core30_150(panel_150u·5폴드×5시드)"},
    "hypothesis": ("라벨·변환·HP·앙상블·유니버스·표본가중이 전부 소진된 뒤 남은 미시험 축은 "
                   "**피처의 시간 변화율**이다. 패널 스크린 실측(2026-09-29, panel_150u 210피처): "
                   "누수 의심 0 · 종목상수 50 · 시장레벨 50 · 무정보 141/210, 시간가변 최고군이 "
                   "변동성·거래량비 계열이고 |IC| t 가 대부분 3 미만 → '수준'이 이미 약하니 "
                   "'기울기(Δ)'가 남은 정보일 수 있다."),
    "evidence": ("panel_feature_screen 실측(2026-09-29 04:2x, /app/reports/overnight/panel_screen_150u.json): "
                 "최고 단일피처 AUC sns_momentum_score_corr0 0.5379(시간가변) · 시간가변 최고군 "
                 "volatility_20d 0.4819(IC t 1.82) · kalman_volatility 0.4826(t 2.34) · "
                 "similar_stocks_return_avg 0.5209(t 6.02). 즉 수준(level)의 단변량 edge 는 "
                 "|AUC−0.5| ≤ 0.038 로 선별 문턱(0.044) 미만이다."),
    "method": ("① 소스 6개를 **이름으로 사전 등록**(volatility_20d·volatility_60d·atr_pct·"
               "volume_ratio_5·rsi·ma_position_20) ② Δ1·Δ5 를 종목별 과거 행만 써서 파생"
               "(shift(+k), 라벨 결측 제거 **전** 계산) ③ 선별은 **파생을 후보에서 제외하고** edge "
               "top30 을 고른 뒤 파생 12개를 강제 포함 → 두 arm 의 top30 이 동일 집합이 되어 "
               "'선별 교체'가 아니라 '추가 정보'를 잰다 ④ panel_150u 서로소 5구간 짝 Δ 로 판정"
               "(CG13 실측: 유니버스 교체만으로 폴드 평균 Δ0.0287 → 단일 arm vs 단일 대조군 금지)"),
    "command": CMD,
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "arm": "DDd_00_30",
    "counterfactual": "DDn_00_30",
    "pairs": [[f"DDd_{a:02d}_{b}", f"DDn_{a:02d}_{b}"] for a, b in SLICES],
    "success": ("구간 짝 Δ(DDd − DDn) 평균 ≥ +0.02 그리고 양(+) 구간 ≥ 4/5 → 신호. "
                "미달이면 '피처 시간 변화율 축 소진'으로 기록한다. "
                "⚠ 파생이 모델에 실제로 들어갔는지(선별 42 = top30 + Δ12)를 로그로 함께 확인하라 — "
                "0개 진입이면 '효과 없음'이 아니라 '선별 미진입'이다."),
    "est_minutes": 30,
    "cost": ("10 config×5폴드×5시드=250 cell · 실측 2초/cell(CG24·CG23) → 유휴 약 12분·경쟁 시 약 30분. "
             "컨테이너 timeout 3600. U3(20:35 런처) 와 같은 슬롯을 두고 경쟁하지 않는다(구동기 lock)."),
    "caution": ("Δ 정의는 scripts/_derived_feature_test.py 로 npz 원본과 비트 일치를 확인했다(14/14 PASS). "
                "core48 게이트는 파생을 잘라내므로 실험 config 한정으로 파생만 통과시킨다"
                "(프로덕션 경로 무변경). 추론 경로(app/inference)에 Δ 를 배포하려면 배치 추론이 "
                "선행 조건이다 — rank 변환과 같은 계약 변경이라 승인 대상."),
    "note": ("2026-09-29 04:3x 신설 — 라벨 꼬리(CG21/22/24)·표본 가중(CG23, 노이즈) 종료 후 "
             "남은 미시험 축. 구현·검증: add_derived()(wf_label_sweep) + 회귀 테스트 14/14 PASS + "
             "스모크 arm 42피처 확인. 회귀 검증: 같은 config(CO_core30_150)의 재측정이 CG23 의 "
             "폴드값과 비트 동일해야 기존 경로 무변경이 증명된다."),
    "updated": "2026-09-29T04:55:00+09:00",
}


def main(path):
    doc = json.load(open(path))
    items = doc["items"] if isinstance(doc, dict) else doc
    ids = {i.get("id") for i in items}
    if "CG25" in ids:
        for i, it in enumerate(items):
            if it.get("id") == "CG25":
                items[i] = ITEM
        print("CG25 기존 항목 갱신")
    else:
        items.append(ITEM)
        print("CG25 신규 추가")
    json.dump(doc, open(path, "w"), ensure_ascii=False, indent=2)
    print("status", ITEM["status"], "priority", ITEM["priority"],
          "est_minutes", ITEM["est_minutes"], "config", len(IDS), "pairs", len(ITEM["pairs"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/QUANT_MODEL_BACKLOG.json")
