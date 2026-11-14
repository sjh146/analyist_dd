#!/usr/bin/env python3
"""2016-11-14 틱 백로그 갱신 — CG277 종결(측정기 부적합) · CG278 신규/완료 · CG279 등록."""
import json
import os

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"] if isinstance(d, dict) else d

by = {i["id"]: i for i in items}

# ── CG277: 덤프 유니버스가 라벨 극단만 담는다는 것을 확인 → 돈 수치는 '조건부'로만 유효
cg277 = by["CG277"]
cg277["status"] = "done"
cg277["command"] = ("bash scripts/cg277_money_horizon.sh  # wf_label_sweep --dump-preds (2 arm) "
                    "+ fillable_topk_horizon_sweep")
cg277["summary_out"] = "/app/reports/overnight/cg277_money_horizon.json"
cg277["result"] = {
    "verdict": "노이즈 + 측정기 결함 발견 — 지평 축을 돈 근거로 종결(CG278 에서 측정기 수리 후 재측정)",
    "detail": (
        "같은 덤프·5폴드×3시드·panel_prod200·230세션·exit close_h·체결성 ON·수수료 0.21%p. "
        "CG276_h1(arm) top-k − 세션 풀평균: h1 k3 -0.092(t-0.50) k5 +0.019 k10 +0.100(t0.88) "
        "k20 +0.145(t1.83) k30 +0.108(t1.76) · h3 k3 -0.305 k10 +0.195(t1.15) k20 +0.137 · "
        "h5 k3 -0.397 k10 +0.336(t1.58) · h10 k3 -0.719 · 전 k 에서 앞/뒤 절반 부호 불일치(앞 +0.17~+0.22 / 뒤 -0.04~-0.08). "
        "최고 Δ +0.145%p(k20, t1.83) < 사전문턱 → 노이즈. "
        "⚠ 측정기 결함: 이 덤프는 `d = d[~isna(_y)]` 뒤에 생성돼 세션당 118행(=200종목의 59%, y_true 정확히 50/50)만 "
        "담는다 — 후보집합이 '미래 상·하위 30%'로 미리 걸러진 유니버스이므로 풀평균이 거래가능 풀평균이 아니다. "
        "→ 배포가능 arm 은 CG278(전 유니버스 덤프)에서 재측정."),
    "delta": None, "per_exp": None, "rc": 0,
}
cg277.setdefault("attempts", []).append({
    "ts": "2026-11-14T18:05:00+09:00", "rc": 0,
    "verdict": cg277["result"]["verdict"], "detail": "스윕 18:03 · 돈집계 18:04 · 합계 4.2분",
    "log": "data/reports/me_cycle/logs/cg277_money_horizon.log", "elapsed_min": 4.2})

# ── CG278: 측정기 수리 후 배포가능 arm 재측정
cg278 = {
    "id": "CG278", "status": "done", "priority": 1, "kind": "money", "owner": "engineer",
    "affects_model": False, "metric": "fillable_topk_vs_pool",
    "title": "[돈 지표·측정기 수리] 덤프가 라벨 극단만 담던 문제를 고쳐(--dump-all-codes) 배포가능 arm 의 초과기대를 전 유니버스에서 재측정",
    "hypothesis": ("CG277 에서 확인: wf_label_sweep 덤프는 라벨 결측(중간 분위)을 버린 뒤 만들어져 후보집합이 "
                   "'미래 상·하위 30%'다(세션 118행·y_true 50/50). 그 위에서 잰 Δ 는 실전 초과기대가 아니다. "
                   "전 유니버스(200종목)를 같은 모델로 채점해 풀평균을 거래가능 풀로 바꾸면, 배포가능 최고 arm "
                   "CO_smooth_d1_h5(top30·depth1·스무딩)의 돈 우위가 유지되는지/사라지는지가 이 축의 최종 판정이다."),
    "counterfactual": "세션 풀평균(전 유니버스·체결성 통과 종목 동일비중 − 수수료) = 무작위 k 기대",
    "command": "bash scripts/cg278_money_allcodes.sh",
    "summary_out": "/app/reports/overnight/cg278_money_allcodes.json",
    "paired_reference": "세션 풀평균(전 유니버스)",
    "success": "k∈{3,5,10} 중 하나라도 Δ(top-k − 풀평균) ≥ +0.1%p/세션 & t ≥ 2 & 앞/뒤 절반 부호 동일",
    "expected": "Δ 가 0 근처로 내려앉음(앞선 CG96~CG116 의 '모델 랭킹은 베타를 못 넘는다'와 정합)",
    "cost": "낮음(5폴드×3시드 4분 + 집계 1분)",
    "est_minutes": 8,
    "created_at": "2026-11-14T18:06:00+09:00",
    "result": {
        "verdict": "사전등록 미충족(분할 부호 불일치) — 평균은 유의하나 시간 재현 없음. 측정기 수리 자체는 완료",
        "detail": (
            "전 유니버스 덤프(2 arm × 5폴드×3시드 · 225세션 · fillable 풀 중앙값 76종목 · 수수료 0.21%p · exit close_h). "
            "CO_smooth_d1_h5 h5: 널(풀평균) -0.071%p/세션(t-0.19) · k3 arm +1.631 → Δ+1.703(t 3.50 · 132/225세션 양) · "
            "k5 Δ+0.902(t 2.60) · k10 Δ+0.539(t 2.27) · k20 Δ+0.395(t 2.38) · k30 Δ+0.259(t 2.02). "
            "지평 사다리(k3): h1 +0.479(t2.45) · h3 +1.272(t3.43) · h5 +1.703(t3.50) · h10 +1.534(t2.48). "
            "⚠ 그러나 앞/뒤 절반: k3 앞 +3.356(t4.33) / 뒤 -0.078(t-0.11) · 폴드 양세션 fold1 38/45 vs fold2 16/45 · "
            "월별 Δ 9/12개월 양(+)이나 개별 유의는 2025-11 +6.285(t3.65) · 2026-02 +3.657(t2.24) 뿐이고 "
            "최근 2026-08 -0.391 · 2026-09 -1.065(n=8). → 사전등록(양쪽 절반 동부호) 미충족, '기간 의존'이 정직한 판독. "
            "부수 확인: 라벨극단 유니버스에서도 h5 k3 Δ+1.759(t3.51)로 사실상 동일 → 제한 유니버스가 Δ 를 부풀린 것은 아니다."),
        "delta": 1.703, "per_exp": None, "rc": 0,
    },
    "attempts": [{
        "ts": "2026-11-14T18:09:12+09:00", "rc": 0,
        "verdict": "사전등록 미충족(분할 부호 불일치) — 평균은 유의하나 시간 재현 없음",
        "detail": "스윕+집계 5.5분 · 세션 225 · 풀평균 -0.071%p · k3 Δ+1.703(t3.50) · 분기 분해 별도(_cg278_period_split.py)",
        "log": "data/reports/me_cycle/logs/cg278_money_allcodes.log", "elapsed_min": 5.5}],
    "note": ("--dump-all-codes 는 wf_label_sweep 신규 플래그(기본 OFF = 기존 실행 비트 동일 · 스모크 2폴드×1시드로 "
             "y_true=None 35,855행 확인). 같은 (날짜,종목) 중복행은 라벨행 우선으로 제거. "
             "이 플래그가 없으면 앞으로의 모든 돈 Δ 는 '미래 극단 유니버스' 조건부 수치다."),
}

# ── CG279: 다음 가설 — 시간 안정성/국면 의존 확정
cg279 = {
    "id": "CG279", "status": "pending", "priority": 1, "kind": "money", "owner": "engineer",
    "affects_model": False, "metric": "fillable_topk_vs_pool",
    "title": "[돈 지표·시간 안정성] 배포가능 arm 초과기대(+1.70%p/5일·t3.50)가 '국면 의존'인지 '표본 부족'인지 가른다 — 블록 부트스트랩 + 최근 6개월 단독 검정",
    "hypothesis": ("CG278 실측: h5 k3 Δ+1.703(t 3.50)인데 앞/뒤 절반이 +3.36/-0.08, 월별로는 12개월 중 9개 양(+)이고 "
                   "2025-11(+6.29) · 2026-02(+3.66) 두 달이 평균을 캐리한다. 세션 Δ 의 sd 가 7.3%p 로 커서 "
                   "단순 t 는 자기상관(같은 종목이 연속 세션에 뽑힘)을 무시한다 — 이동블록 부트스트랩(블록 10세션)으로 "
                   "재검정하면 유의성이 사라질 수 있다. 최근 6개월(2026-04~09)만 보면 ΣΔ 부호가 음(-)일 가능성이 크다."),
    "counterfactual": "같은 덤프의 세션 풀평균 + 무작위 k(풀에서 k개 무작위 추출) 분포",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg279_block_bootstrap.py "
                "--jsonl /app/reports/overnight/cg278_preds_allcodes.jsonl --tag CO_smooth_d1_h5 --k 3 "
                "--horizon 5 --block 10 --reps 5000 --since 2026-04-01 "
                "--json-out /app/reports/overnight/cg279_block_bootstrap.json'"),
    "summary_out": "/app/reports/overnight/cg279_block_bootstrap.json",
    "paired_reference": "세션 풀평균",
    "success": ("블록 부트스트랩 95% CI 하한 > 0 이고 최근 6개월 구간 Δ > 0 이면 '시간 안정' → 배포 후보 검토(승격 아님) / "
                "CI 가 0 을 포함하거나 최근 구간 Δ ≤ 0 이면 '국면 의존'으로 돈 축 종결"),
    "expected": "CI 가 0 을 포함(국면 의존) — 최근 6개월 Δ ≤ 0",
    "cost": "낮음(새 학습 0 · 같은 덤프 재집계 1~2분)",
    "est_minutes": 3,
    "created_at": "2026-11-14T18:30:00+09:00",
    "setup_needed": ("`scripts/_cg279_block_bootstrap.py` 신설 필요(다음 틱 자율): fillable_topk_expectancy 의 "
                     "load_rows/enrich/apply_filters/baskets/pool_series 재사용 → 세션별 Δ 배열 → 이동블록 부트스트랩 "
                     "5000회(블록 10세션)로 CI, --since 로 최근 구간 단독 Δ·t. 판정기는 기존 fillable_topk_vs_pool 재사용."),
}
if "CG278" not in by:
    items.append(cg278)
if "CG279" not in by:
    items.append(cg279)

d["updated_at"] = "2026-11-14T18:30:00+09:00"
dst = P + ".tmp"
with open(dst, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
os.replace(dst, P)
print("ok", len(items), "items")
