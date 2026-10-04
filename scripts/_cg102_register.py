import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]

xr26_note = (
    " | 2026-10-05 01:2x 엔지니어 오프라인 재현·수리 증명 (실 KIS 호출 없음, "
    "`python3 scripts/_xr26_pagination_proof.py` → **5/5 PASS**): "
    "결함 2성분 확정 — ① `kis_client.get_minute_chart`(services/kis-collector/kis_app/client/kis_client.py:392-410)"
    " 는 `fid_cnt` 를 **인자로 받기만 하고 params 에 넣지 않는다** → KIS 는 1회 30봉 상한으로 응답하는데 "
    "`minute_collector.collect_stock` 의 종료조건 `len(page_bars) < int(fid_cnt)` 와 기본값 "
    "`FID_CNT_DEFAULT=100`(minute_collector.py:23, :90) 때문에 **첫 페이지에서 break** "
    "(mock 재현: 봉 30개 · 150100~153000 · API 1콜) "
    "② `MAX_PAGES_DEFAULT=10`(minute_collector.py:24)도 상향 필요 — fid_cnt=30 으로만 고치고 max_pages=10 을 두면 "
    "300봉(103100~153000)에서 잘린다(재현 확인). "
    "수리안: `FID_CNT_DEFAULT = 30`, `MAX_PAGES_DEFAULT = 20` (또는 종료조건을 '직전 페이지 대비 신규 0건'으로) "
    "→ 전 구간 391봉 · 14콜 · 09:30 이전 31봉 수집(증명 B). 빈 페이지에서 무한루프 없이 종료(증명 D). "
    "커버리지 30→391봉 = **13.0배**. 호출량 영향: 종목당 1콜 → **14콜/일**, 300종목이면 ≈4,200콜/일 "
    "→ net_guard 간격 정책 안에서 장 마감 후 분할 스케줄 필요(수집기 소유자 몫). "
    "DB 실측 확인: minute_bars 45,120행 · 6거래일 · 301종목 · time 150100~153000(30개) · **time<='093000' 0행**."
)

cg102 = {
    "id": "CG102",
    "title": "공시 이벤트 피처 4종 청정 패널 스크린 — 원천이 1,712행→214,982행으로 커진 뒤 재측정 (데이터 축)",
    "status": "done",
    "priority": 2,
    "hypothesis": (
        "CG67(2026-10-02)의 '공시 기여 0' 결론은 원천 1,712행·49종목(패널 셀의 42.2%만 비영) 위의 결론이다. "
        "`disclosures` 가 214,982행·3,056종목·427 rcept_dt·2025-01-02~2026-10-02(월 5.7k~27k행) 로 커진 지금, "
        "공시 이벤트 스트림(건수·경과일·자본조달성)은 가격·수급과 독립인 새 정보 클래스일 수 있다. "
        "rcept_dt 는 공시 시점 그 자체라 as-of 가정(90/45일)이 필요 없어 무누수가 구조적으로 보장된다."
    ),
    "counterfactual": "같은 패널(panel_prod200)의 기존 213피처 — 선별 문턱 |AUC−0.5| ≥ 0.044",
    "success": "신규 4종 중 1종 이상 단일피처 |AUC−0.5| ≥ 0.044 (시간가변 확인 동반)",
    "command": (
        "docker exec stock_xgboost_ml python /app/scripts/patch_panel_disclosures_multi.py "
        "--in app/models/wf/panel_prod200.npz --out app/models/wf/panel_prod200_disc.npz && "
        "docker exec stock_xgboost_ml python /app/scripts/panel_feature_screen.py "
        "--panel app/models/wf/panel_prod200_disc.npz --horizon 5 --q 0.30 --top 40 "
        "--out /app/reports/overnight/panel_prod200_disc_screen.json"
    ),
    "est_minutes": 4,
    "result": {
        "verdict": "노이즈(축 종결)",
        "detail": (
            "청정 패널 panel_prod200(54,800행×213피처·200종목·274날짜)에 공시 이벤트 4종을 as-of(rcept_dt<=date)로 "
            "append해 단일피처 스크린(라벨 h5·q0.30·32,401 유효행·274 IC일): "
            "disc_ev_count_5d AUC 0.5026(|Δ0.5| 0.0026 · IC +0.0267 t 3.88) · "
            "disc_ev_count_20d 0.5006(0.0006 · IC +0.0158 t 2.48) · "
            "disc_ev_cap_20d 0.4964(0.0036 · IC +0.0060 t 0.88) · "
            "disc_ev_days_since 0.4958(0.0042 · IC −0.0160 t −2.83) — 4종 모두 **시간가변**(비영 12.2~84.4%). "
            "최대 |AUC−0.5| = 0.0042 로 선별 문턱 0.044 의 **1/10** → top-30 선별에 진입할 수 없다 "
            "(= AUC 를 움직일 경로가 없다). IC 는 count 계열이 통계적으로 유의하나 크기가 +0.027 수준이고 "
            "풀링 AUC 는 0.50 이다 — '유의하지만 쓸 수 없음'(선별은 풀링 edge 로 한다). "
            "같은 스크린의 패널 전체 상태: 누수 0 · 종목상수 34 · 시장레벨 56 · **무정보 123/217** · "
            "최고 |AUC−0.5| 는 SNS 계열(kalman_attention/sns_attention_score 0.6071)이나 **fill 2.2%** 라 "
            "희석계수(1−0.978²≈0.044)를 곱하면 edge ≈0.005 로 역시 선별 불가."
        ),
        "per_exp": None,
        "delta": None,
    },
    "note": (
        "2026-10-05 01:3x 등록·실행(장외 자율 틱, panel_feature_screen 3.5분 — 패널 재빌드 없이 컬럼만 append). "
        "부수 데이터품질 실측: `disclosures` 에 (stock_code, rcept_dt) 다중행 그룹이 **44,302개**이고 "
        "005930 은 2026-02-06 하루에 **933행**(서로 다른 rcept_no, report_nm='임원ㆍ주요주주특정증권등소유상황보고서') "
        "→ 건수형 피처는 대량 동일자 보고서에 지배된다(최대 451건/5일). 정제판(일자별 존재여부·임원소유 보고서 제외)을 "
        "만들 수는 있으나 최대 edge 0.0042 라 회수 가치가 없다 — **그래서 이 축은 정제 없이 닫는다**. "
        "CG67 의 '부활=성능 아님' 결론은 커버리지 120배 확대 후에도 유지됨을 확인했다(결론은 같고 근거는 강해졌다). "
        "재현물: scripts/patch_panel_disclosures_multi.py · 서비스 컨테이너 산출물 reports/overnight/panel_prod200_disc_screen.json."
    ),
}

for i in items:
    if i["id"] == "XR26":
        if "2026-10-05 01:2x 엔지니어 오프라인 재현" not in i.get("note", ""):
            i["note"] = i.get("note", "") + xr26_note
        i["status"] = "needs_setup"

if not any(i["id"] == "CG102" for i in items):
    items.append(cg102)

d["items"] = items
with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

# 검증: 리바인딩 함정 방지 — 다시 읽어 확인
d2 = json.load(open(P, encoding="utf-8"))
assert any(i["id"] == "CG102" for i in d2["items"]), "CG102 등록 실패"
assert any("오프라인 재현" in i.get("note", "") for i in d2["items"] if i["id"] == "XR26"), "XR26 note 갱신 실패"
print("OK · items", len(d2["items"]), "· CG102 status", [i["status"] for i in d2["items"] if i["id"] == "CG102"])
