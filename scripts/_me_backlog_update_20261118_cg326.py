#!/usr/bin/env python3
"""CG326 기록(2026-11-18 06:4x) — 미배선 원천 스윕을 **신선 패널**로 재측정한 결과.

배경: 틱 출력의 '미배선 원천 스윕' 캐시는 낡은 패널(panel_prod200) 유니버스로 커버리지를 재서
news::news_events 0.13 · short_selling 0.125 같은 값을 계속 보고한다(CG323 이 short_selling 이
측정 artifact 였음을 실측). `source_coverage_sweep.py --panel` 로 신선 패널 기준으로 다시 쟀다.
"""
import json

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
TS = "2026-11-18T06:45:00+09:00"

item = {
    "id": "CG326",
    "status": "done",
    "priority": 2,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "source_coverage_sweep",
    "title": (
        "[측정정합성] 원천 커버리지를 **신선 패널(200종목)** 로 재측정 — 틱의 '커버리지 부족' 목록이 "
        "낡은 패널 유니버스 때문에 과소평가돼 있었다"
    ),
    "hypothesis": (
        "스윕 캐시가 panel_prod200(구간 ~2026-09-23) 유니버스로 커버리지를 재면 신규 수집분이 구조적으로 "
        "0.1 대로 보인다(CG323 실측: short_selling 0.125→0.880). 같은 스윕을 현재 쓰는 신선 패널로 다시 재면 "
        "'남은 레버는 수집 데이터뿐'이라는 결론의 근거가 달라진다."
    ),
    "command": (
        "python3 scripts/source_coverage_sweep.py "
        "--panel /app/app/models/wf/panel_200liq_ffill_2605_ss_tv.npz "
        "--json reports/overnight/cg326_coverage.json"
    ),
    "counterfactual": "틱 캐시(panel_prod200 기준: news_events 0.13 · short_selling 0.125 · intraday 0.115)",
    "success": "각 CLOSED/BLOCKED 원천의 panel_cover_frac 을 신선 패널 기준으로 재고, 결론(신규 후보 유무)이 유지되는지 숫자로 남긴다.",
    "result": {
        "verdict": "보고값 정정 — 신규 후보는 여전히 0",
        "detail": (
            "신선 패널(200종목·112거래일) 기준 재측정: news_events cover **0.54**(틱 표기 0.13) · "
            "sns_posts 0.90 · sns_post_features 0.875 · foreign_institutional 0.97 · "
            "krx_short_selling 0.88 · minute_bars 0.82(단 16거래일) · minute_close_bars 0.195(6거래일) · "
            "ownership 0.97(17거래일) · stock_sentiment 0.24. 게이트(cover≥0.75 & days≥250) 통과는 "
            "foreign_institutional(306일)·sns_posts(534일) 뿐이고 둘 다 **이미 피처로 배선**돼 있다"
            "(institution_net_buy_5d·foreign_net_buy_5d·sentiment_*). → 신규 후보 0 은 유지되지만, "
            "BLOCKED 근거는 '커버리지 부족'이 아니라 **이력 길이**다(minute_bars 16일 · minute_close_bars 6일)."
        ),
        "rc": 0,
        "artifacts": ["reports/overnight/cg326_coverage.json"],
    },
    "note": (
        "2026-11-18 06:4x 엔지니어 자율(장외 틱). 코드 변경 0(기존 --panel 옵션 사용). "
        "함의: 남은 데이터 레버는 (a) 분봉 이력 확대(XR26 페이지네이션 수리 → 수집기 소유·승인 대상) "
        "(b) 250일 이력이 쌓이기 전까지는 '신규 원천'이 아니라 '기존 원천의 다른 가공'만 가능. "
        "⚠ 틱의 미배선 스윕 캐시가 언제 갱신되는지 확인 필요 — 낡은 커버리지로 '레버 없음'을 반복 보고하고 있다."
    ),
    "created_at": TS,
    "finished_at": TS,
    "attempts": 1,
    "needs_approval": False,
}

# 같은 id 가 이미 있으면 교체(재실행 안전)
items = [i for i in d["items"] if i.get("id") != "CG326"] + [item]
d["items"] = items
d["updated_at"] = TS
d["updated"] = TS
json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("wrote CG326 · items:", len(items))
