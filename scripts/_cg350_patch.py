import json, shutil

p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(p, p + ".bak_cg350")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]

cg349 = next(i for i in items if i["id"] == "CG349")
cg349["status"] = "failed"
cg349["result"] = {
    "verdict": "실행실패 — 창 격자에 train_end 이후 창이 없음(프로토콜 한계, 측정값 없음)",
    "detail": ("train_end=2026-10-01 로 고정하니 5창 전부 학습구간과 겹쳐 OOS 창 0개 → rc=1 "
               "('학습구간 이전 창이 없습니다'). 원인: 창 격자는 2026-01-16~2026-11-11 의 고정 5창이고 "
               "마지막 창이 2026-09-11 에 **시작**한다 → 10-01 이후 구간(2026-10-02~11-11)을 채점하는 "
               "창이 격자에 없다. 챔피언(train 2026-07-03~10-01)과 후보(train 2025-09-24~08-27)는 "
               "학습구간이 달라 **두 모델 모두에게 OOS 인 겹침 창이 존재하지 않는다** — "
               "현 프로토콜로는 챔피언 vs 후보 짝 비교 자체가 불가능하다(계측기 실패가 아니라 격자 한계)."),
    "rc": 1,
    "delta": None,
}
cg349["followup"] = "CG350 — 창 절단 옵션(프로토콜 변경, 리뷰보드 승인 대상)"

cg350 = {
    "id": "CG350",
    "title": ("[프로토콜·승인대기] champion_robust_eval 창 격자에 '학습구간 이후 부분창' 절단 옵션 추가 "
              "— 챔피언과 재학습 후보를 같은 날짜창에서 짝 비교하기 위한 선행조건"),
    "status": "needs_setup",
    "priority": 1,
    "est_minutes": 40,
    "owner": "model_engineer",
    "counterfactual": ("CG349 실측: 창 격자(2026-01-16~2026-11-11, 5창)에 train_end 2026-10-01 이후 "
                       "창이 없어 OOS 창 0개 → rc=1. 챔피언(2026-07-03~10-01 학습)과 후보(2025-09-24~"
                       "08-27 학습)에게 공통인 OOS 구간은 2026-10-02~11-11 뿐인데 격자가 그 구간을 "
                       "시작점으로 잡지 못한다."),
    "success": ("① --allow-partial-post-train 옵션(기본 OFF = 현행 유지)으로 겹치는 창을 train_end+1 "
                "부터 절단해 부분창으로 채점 ② 두 arm 이 같은 절단 창 서명을 갖는지 window_mismatch=[] 로 "
                "확인 ③ 3시드 짝 Δ SE ≤ 0.010 이면 +0.02 해상 가능 판정."),
    "expected": "챔피언 vs 후보 비교가 처음으로 같은 창에서 성립 — 배포 챔피언 교체 판단의 측정 근거 확보.",
    "created": "2026-11-19T06:56:00+09:00",
    "setup_needed": ("리뷰보드 승인 필요(프로토콜 변경): champion_robust_eval 의 OOS 창 정의를 바꾼다. "
                     "기본값은 현행 유지(옵션 OFF)이므로 기존 기준선 수치는 불변. 승인 후 내가 구현할 것: "
                     "① _split_oos 에 clip_partial 인자 ② --allow-partial-post-train 플래그 ③ "
                     "회귀 테스트(기존 창 결과 비트 동일) ④ CG349 재실행."),
    "rationale": ("측정 해상도 문제(CG347)와 창 불일치(CG348)를 잇는 마지막 블로커다. 이 옵션 없이는 "
                  "챔피언 대비 어떤 후보도 '같은 창'에서 비교된 적이 없다 — 20사이클 무개선 카운터의 "
                  "근거 자체가 프로토콜 혼합 위에 서 있다."),
}
if not any(i["id"] == "CG350" for i in items):
    items.insert(0, cg350)

json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG349 →", cg349["status"], "| CG350 추가:", cg350["status"])
