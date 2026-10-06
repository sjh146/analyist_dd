#!/usr/bin/env python3
"""2026-10-06 장외 자율(엔지니어) — XR26 재검증 기록 + CG129(수리 후 인트라데이 스크린) 등록.

규칙(스킬): ① 항목을 mutate 할 때는 **in-place** 로 (리스트 리바인딩 금지) ② write 후 같은 파일을
다시 읽어 존재를 assert ③ indent=2 · ensure_ascii=False.
"""
import json

PATH = "docs/QUANT_MODEL_BACKLOG.json"
NEW_ID = "CG129"

XR26_NOTE_ADD = (
    " | 2026-10-06 16:1x 재검증(엔지니어·장외 자율): 수리안 확정 — `python3 scripts/_xr26_pagination_proof.py` "
    "5/5 PASS(오프라인·KIS 호출 0·DB 쓰기 0). 실측: 현행 기본값 = 30봉(15:01~15:30)·1콜 / 수리(fid_cnt=30, "
    "max_pages=20) = 391봉(09:00~15:30)·14콜 = **13.0배** / fid_cnt=30 이어도 max_pages=10 이면 300봉 미달"
    "(상향 필수) / 빈 페이지에서 무한루프 없이 종료. 필요한 변경은 minute_collector.py 2곳뿐 — ① 기본 "
    "fid_cnt 를 실제 API 페이지 상한(30)과 맞추거나 종료조건을 '직전 페이지 대비 신규 0건'으로 교체"
    "(현 종료조건 `len(page_bars) < int(fid_cnt)` 가 100 기준이라 첫 페이지에서 항상 break) ② "
    "`MAX_PAGES_DEFAULT = 10` → 20. 승인 즉시 적용→백필→CG129 착수. 콜량 = 300종목×≈14콜 ≈ 4,200/일 "
    "→ net_guard 프로세스 간격 정책 안에서 마감 후 분할 스케줄 필요."
)

CG129 = {
    "id": NEW_ID,
    "title": "분봉 페이지네이션 수리(XR26) 후 인트라데이 피처 스크린 재측정 — 6일·30봉 표본 한계 해소",
    "status": "needs_setup",
    "priority": 1,
    "metric": "intraday_screen",
    "arm": None,
    "counterfactual": "일봉 유도 피처(return_1d·gap·day_range — 같은 행) + 동전 AUC 0.5",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u "
                "scripts/intraday_feature_screen.py --json-out /app/reports/overnight/"
                "cg129_intraday_screen.json'"),
    "hypothesis": (
        "CG128 은 minute_bars 가 종목당 30봉(15:01~15:30)·6거래일뿐이라 n_dates 4 < 20 으로 판정불가였다. "
        "XR26 수리로 전 구간(391봉)이 적재되면 날짜별 IC 검정이 성립한다 — '장중 흐름(오전/오후 구간 "
        "수익률·거래량 프로파일·종가 위치)'이 다음날 방향에 정보를 갖는가."
    ),
    "evidence": (
        "CG128 판정불가(n_dates 4 · 최강 return_1d IC -0.1052 t -2.61 — 표본 부족으로 문턱 미달, 문턱 하향 "
        "금지). XR26 오프라인 증명 5/5 PASS: 현행 30봉 vs 수리 391봉 = 13.0배. 인트라데이는 커버리지 미확보 "
        "+ edge 미측정 = 기록상 유일한 미지 영역이다(다른 미사용 원천은 '커버리지 부족' 또는 'edge 0' 으로 "
        "확정 실측)."
    ),
    "success": ("사전등록(변경 금지): 날짜별 IC |평균| >= 0.03 AND |t| >= 2 AND n_dates >= 20 인 피처 >= 1개 "
                "→ '정보있음'. n_dates < 20 이면 다시 판정불가로 적고 축을 닫는다."),
    "expected": "미지 — 유일하게 미측정인 데이터 클래스",
    "cost": "백필(밤 1회 ≈4,200콜) + 스크린 2분",
    "est_minutes": 5,
    "note": (
        "선행 조건: XR26(수집기 소유·승인 대상) 수리 + 분봉 백필. XR26 미수리 상태로 이 항목을 pending 으로 "
        "올리면 같은 30봉 표본을 다시 재는 무의미한 실행이 된다(억지 실행 = 축 재시험). metric intraday_screen "
        "은 CG128 에서 구동기 배선 완료(파서·판정기·경로·자체점검 16/16)."
    ),
    "attempts": [],
    "result": None,
}


def main():
    d = json.load(open(PATH, encoding="utf-8"))
    items = d["items"]
    assert not any(i.get("id") == NEW_ID for i in items), f"{NEW_ID} 이미 존재"

    # in-place mutate (리스트 리바인딩 금지)
    hit = None
    for it in items:
        if it.get("id") == "XR26":
            hit = it
            it["note"] = (it.get("note") or "") + XR26_NOTE_ADD
    assert hit is not None, "XR26 없음"

    items.append(CG129)
    d["updated_at"] = "2026-10-06T16:15:00+09:00"
    if "updated" in d:
        d["updated"] = "2026-10-06"

    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 재읽기 assert (조용한 등록 실패 방지)
    d2 = json.load(open(PATH, encoding="utf-8"))
    ids = [i["id"] for i in d2["items"]]
    assert NEW_ID in ids, "등록 실패"
    assert "13.0배" in [i for i in d2["items"] if i["id"] == "XR26"][0]["note"], "XR26 note 미반영"
    print(f"OK: XR26 note 갱신 · {NEW_ID} 등록 · total items {len(ids)}")


if __name__ == "__main__":
    main()
