#!/usr/bin/env python3
"""일회성: 백로그 CG73 정정 + CG75 신설 (indent=2 로 재직렬화 — diff 폭발 방지)."""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak_cg75")

d = json.load(open(P))
items = d["items"]

CORRECTION = (
    " | 2026-10-03 정정(실측, panel_prod200·읽기 전용): ① **원천 이력이 짧다** — "
    "sns_posts(posted_at) 월별 2025-08~2026-04 는 월 1~9행뿐이고 실질 시작은 "
    "2026-06(4,590행)→07(119,719)→08(109,553)→09(150,758) = 사용 가능 이력 ≈ **4개월**(종목수 117→180→194→308). "
    "news_events 는 2026-04~10(월 477~2,406행·33~166종목). → 420일 패널의 폴드 전체를 덮을 수 없다(최근 폴드만). "
    "② 낮은 교집합의 절반은 '수집 범위'가 아니라 **유니버스 표집**이다 — sns_posts 309종목 = KOSPI 159·KOSDAQ 150 혼합, "
    "패널 유니버스는 select_training_universe 시드랜덤 200종목 → 기대교집합 ≈ 200×309/2655 ≈ 23 (실측 22). "
    "종목코드 형식은 전부 6자리 숫자(309/309) → **CG60/CG67 유형의 조인·배선 결함이 아니다**. "
    "③ 그 유니버스 정렬 자체는 CG57 실측 Δ−0.0039(노이즈)로 이미 종결됐다 → 정렬로는 이득이 없다. "
    "→ 재개 조건은 '수집 범위 확장'만이 아니라 **이력 축적(또는 패널 창 단축)** 이다. "
    "4개월 창은 U3b 실측(90일 창 std 0.0556)에서 문턱 +0.02 를 판정할 수 없다."
)

for it in items:
    if it.get("id") == "CG73":
        it["evidence"] = (it.get("evidence") or "") + CORRECTION
        it["setup_needed"] = (
            "선행 조건 2개: ① 수집 범위 — SNS/뉴스가 패널 유니버스를 덮게 확장(수집기 소유·승인 대상) "
            "② **이력 축적** — sns_posts 실질 이력이 2026-06~ = 약 4개월이라 420일 패널 폴드 전체를 덮지 못한다"
            "(4개월 창은 std 0.0556 으로 +0.02 판정 불가, U3b). 즉 확장만으로는 부족하고 누적이 필요하다. "
            "정렬(수집 범위 정합)만으로는 이득 없음(CG57 Δ−0.0039)."
        )
        it["note"] = (it.get("note") or "") + (
            " | 2026-10-03 16:0x 정정: 위 evidence 참조 — '수집 범위 문제'라는 진단은 절반만 맞다"
            "(나머지 절반은 시드랜덤 유니버스 표집, 그리고 무엇보다 **4개월 이력**)."
        )
        print("CG73 updated")

NEW = {
    "id": "CG75",
    "title": "전방(forward) 배포 경로 신호의 사전등록 판정 — 표본 누적 후 모델버전 짝 비교 (CG68 계측기 승격)",
    "status": "backlog",
    "priority": 3,
    "affects_model": True,
    "arm": "배포 챔피언(현행) — 전방 pooled/daily AUC + top-k 실현수익",
    "counterfactual": "동전(0.5) + 전체평균 실현수익(all_ret_mean)",
    "command": "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/forward_scorecard.py --out /app/reports/overnight/forward_scorecard.json",
    "metric": "forward_scorecard",
    "hypothesis": (
        "배포 경로의 confidence 가 실현 선행수익을 예측한다. 즉 창 기반 OOS(0.44~0.53)와 달리 "
        "실거래 경로에는 전방 신호가 존재하며, 누적 표본에서 confidence 상위 종목의 실현수익이 전체평균을 상회한다."
    ),
    "evidence": (
        "2026-10-03 실측(계측기 2회차, 같은 명령): h1 n=17,938페어·5일 pooled 0.5437·daily 0.5290·"
        "top10 −0.257% vs 전체 +0.794% · h5 n=6,404페어·2일 pooled 0.5719·daily 0.5539·"
        "top10 +2.45% vs 전체 +3.01%. 1차(2026-10-02) h1 0.5468/5일 · h5 0.5003/2일. "
        "→ pooled 는 0.5 를 넘지만 **top-k 가 전체평균을 하회**하고 날짜가 2~5개뿐이라 판정 불가(CG68 결론 유지). "
        "2026-10-03 수리: ml_predictions.model_version 이 항상 'v1.0' 이라 승격/롤백 구분이 불가했다 → "
        "predictor._resolve_model_version() 로 챔피언 메타 기반 식별자 귀속(자체점검 10/10 PASS)."
    ),
    "success": (
        "n_dates ≥ 10 거래일에서 h5 전방 pooled AUC ≥ 0.52 **그리고** top-10 실현수익 ≥ all_ret_mean (동시 충족). "
        "미달이면 '배포 경로 전방 신호 없음'으로 종결하고 배포 모델 교체 근거로 쓰지 않는다."
    ),
    "expected": "미지 — 창 기반 OOS 가 0.44~0.53 이므로 전방도 비슷할 가능성이 크다. 다만 이 지표만이 승격/롤백의 실거래 성적을 말할 수 있다.",
    "cost": "실행 1분(읽기 전용) · 표본 누적에 거래일 10일 필요",
    "est_minutes": 1,
    "setup_needed": (
        "① 일 1회 실행 배선(크론 추가 = 승인 대상) 또는 사람이 장 마감 후 수동 실행 "
        "② 표본 누적 대기(n_dates ≥ 10) ③ model_version 귀속(2026-10-03 구현) 이후에만 승격/롤백 전후 짝 비교가 성립"
    ),
    "note": (
        "2026-10-03 16:0x 신설(주말 자율 세션). **오늘 착수하지 않는 이유**: 오늘 다시 돌려도 같은 5일을 "
        "재측정할 뿐이라 Δ=0 이고 '실험을 돌렸다'만 남는다 → precondition 을 명시하고 backlog 로 둔다. "
        "재개 조건이 충족되면 judge 배선과 함께 pending 으로 승격한다(단일 런 비교 금지 — 표본 누적 후 짝 판정)."
    ),
    "result": None,
    "attempts": 0,
}

if not any(it.get("id") == "CG75" for it in items):
    items.append(NEW)
    print("CG75 appended")

d["updated_at"] = "2026-10-03T16:10:00+09:00"

with open(P, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print("items:", len(items))
