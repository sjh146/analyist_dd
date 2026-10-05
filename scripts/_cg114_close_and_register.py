#!/usr/bin/env python3
"""2026-10-05 16:0x 세션 — CG114 축 종결 기록 + CG115/CG116 등록 + 원장 reported 플래그.

- CG114: 사전등록 성공조건(d1 edge ≥ +0.1%p & t≥2 & d9·d10 t<2)은 **미충족** →
  하위 꼬리 반전 축을 노이즈로 확정하고 '돈 축 최종 종결'을 결과에 기록(재시험 금지).
- CG115: 고전 팩터 랭킹 정보 스크린(scripts/factor_money_screen.py, metric factor_money_screen).
- CG116: 그 필터 ON 돈 판정(metric factor_money_fillable) — 사전등록 문턱 명시.
- 원장: CG114 기록에 reported=True / reported_at=지금(다음 틱 중복보고 방지 + 미전달 감지기 짝).
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
PROJ = "/home/jhshi/analyist_dd"
BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")

z = json.load(open(os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg114_seed7_ic.json")))
prof = ((z.get("profile") or {}).get("decile_edge_pct") or {})


def edge(k):
    v = prof.get(k) or {}
    return round(v.get("edge_pct", 0.0), 3), v.get("t")


d1, d1t = edge("1")
d9, d9t = edge("9")
d10, d10t = edge("10")
ic = z.get("ic") or {}

conclusion = (
    "재현 실패 → 하위 꼬리 반전은 노이즈로 확정, **돈 축 최종 종결**(재시험 금지). "
    f"사전등록 조건(① d1 edge ≥ +0.1%p AND ② t ≥ 2 AND ③ d9·d10 t < 2) 중 ①·③ 이 동시에 깨졌다: "
    f"seed7 d1 = {d1:+.3f}%p (t {d1t}) 로 seed0(+1.380 · t 2.08)과 **부호 반전**, "
    f"d9 t {d9t} (≥2 로 ③ 위반). IC 자체는 +{ic.get('mean_ic', 0):.4f} (t {ic.get('t')}) 로 양(+)이지만 "
    "유의 버킷이 최하위 분위에서 상위 분위로 옮겨간 것이 아니라 **부호가 뒤집혔다** = 서로소 유니버스에서 "
    "극단 꼬리는 재현되지 않는다(같은 데이터에서 이미 본 힌트의 비blind 재검정 1회 — 이 항목으로 닫는다)."
)

d = json.load(open(BACKLOG))
ids = {it.get("id") for it in d["items"]}

for it in d["items"]:
    if it.get("id") == "CG114":
        it["result"]["profile_check"] = {
            "seed7_d1": {"edge_pct": d1, "t": d1t},
            "seed7_d9": {"edge_pct": d9, "t": d9t},
            "seed7_d10": {"edge_pct": d10, "t": d10t},
            "seed0_d1_cg113": {"edge_pct": 1.380, "t": 2.08},
            "prereg_met": False,
            "prereg_missing": ["d1 edge ≥ +0.1%p", "상위 분위(d9·d10) t < 2"],
        }
        it["result"]["axis_conclusion"] = conclusion
        it["note"] = (it.get("note", "") +
                      f" | {NOW} 세션 판정: {conclusion}")

CMD115 = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 900 "
          "python scripts/factor_money_screen.py --panel app/models/wf/panel_prod200.npz "
          "--horizon 5 --out reports/overnight/factor_money_screen.json'")
CMD116 = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 900 "
          "python scripts/factor_money_screen.py --panel app/models/wf/panel_prod200.npz "
          "--horizon 5 --fillable --out reports/overnight/factor_money_screen_fillable.json'")

if "CG115" not in ids:
    d["items"].append({
        "id": "CG115",
        "title": "고전 팩터(가치·퀄리티·모멘텀·저변동·멀티팩터) 랭킹 정보 스크린 — '정보가 데이터에 없는가, 모델이 못 뽑는가' 대조군 (기록상 0회)",
        "status": "pending",
        "priority": 2,
        "affects_model": False,
        "arm": "factor_money_screen: panel_prod200(200종목·274세션·h5) · value/quality/momentum/lowvol/multifactor 합성",
        "counterfactual": "무작위 랭킹(IC 기대 0) · 참조: 배포 챔피언 IC +0.0165(t 0.94)·q0.05 +0.0560(t 4.53) (CG113)",
        "hypothesis": ("모델 랭킹의 돈 방향 정보 축이 CG114 로 닫혔으니 남은 질문은 '정보가 데이터에 없는가, "
                       "모델이 못 뽑는가'다. 고전 팩터 프리미엄(가치·퀄리티·모멘텀·저변동)은 문서화된 효과이므로, "
                       "같은 유니버스·기간·라벨에서 팩터 합성의 횡단면 IC 를 재면 모델의 IC 와 직접 비교된다."),
        "method": ("① 패널 유니버스·날짜·가격(2025-08-04~2026-09-23, 200종목, 54,800행)을 기준으로 "
                   "② 재무는 financial_ratio_features 를 rcept_dt as-of 로 조인(실측 위반 0) "
                   "③ 모멘텀(60→5)·저변동(20일)은 패널 가격의 과거만 사용 ④ 날짜별 횡단면 z(winsor 1/99%) 후 "
                   "경제적 부호를 사전 고정해 합성 ⑤ 날짜별 Spearman IC 와 top/bottom 분위 스프레드(%p) 산출."),
        "command": CMD115,
        "metric": "factor_money_screen",
        "check": "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_factor_money_metric_test.py",
        "success": ("사전등록: 멀티팩터 횡단면 IC > 0 이고 t ≥ 2 → '정보있음'(고전 팩터 랭킹에 횡단면 정보가 있다). "
                    "미달이면 '노이즈'. ⚠ 순기대가 아니다(체결성 필터·수수료 미적용) → 승격 근거가 될 수 없다."),
        "expected": "미지 — 모델 IC(0.0165~0.056)와 같은 자릿수면 '데이터에 정보가 있다'로 읽고, 0 이면 데이터 축(수집)이 유일한 레버임을 강화한다.",
        "cost": "실행 30초(읽기 전용, DB 조회 + 패널 가격)",
        "est_minutes": 3,
        "evidence": ("2026-10-05 16:0x 스크립트 스모크(사전등록 전에 값을 봤다 — 비blind): "
                     "value IC +0.0436(t 4.25) · quality +0.0244(4.23) · lowvol +0.0524(4.08) · momentum −0.0218(−2.15) · "
                     "multifactor +0.0275(3.12) 인데 top−bottom 분위 스프레드는 quality −1.10(t−2.31) · "
                     "lowvol −2.65(t−4.36) · multifactor −1.80(t−3.85) · value +0.79(t1.24) "
                     "= **IC 양(+)인데 극단 분위 스프레드는 음(−)** (모델 CG113 과 같은 비단조 서명)."),
        "note": ("도구 신설: scripts/factor_money_screen.py(읽기 전용 — DB as-of + 패널 가격, 외부 스크립트 의존 없음) "
                 "+ metric factor_money_screen 배선(summary_path·parse·judge 3곳, per_exp 미생성) "
                 "+ 자체점검 scripts/_factor_money_metric_test.py(23/23 PASS). "
                 "해석 규율: IC 는 정보이지 순기대가 아니다(승격 금지) · 팩터 부호는 사전 고정(사후 부호 뒤집기 금지). "
                 "미구현분(다음 세션 후보): factor IC 의 분위 프로파일(어느 분위가 IC 를 만드는가) 및 as-of 지연 가정 대조."),
        "added": f"{NOW} (엔지니어 — CG114 돈 축 종결 후 '데이터 vs 모델' 분해 대조군)",
        "attempts": [],
    })

if "CG116" not in ids:
    d["items"].append({
        "id": "CG116",
        "title": "팩터 top-k 의 돈 환산 — 체결성 필터 ON + 널 기준선(세션 풀평균) 대비 베타 초과 검정",
        "status": "pending",
        "priority": 2,
        "affects_model": False,
        "arm": "multifactor top-k(k=3·5) — panel_prod200 · 체결성 필터(당일등락<25%·거래대금≥10억·가격≥1000원) · h5",
        "counterfactual": "세션 풀평균 = 무작위 k 기대(필터 통과 종목 동일비중) — AUC 의 '동전 0.5' 에 해당",
        "hypothesis": ("CG115 는 IC 양(+)·스프레드 음(−)의 비단조 서명을 보였다. 무필터 스프레드는 살 수 없는 행"
                       "(상한가·저유동)이 만든 착시일 수 있으므로(실측 CG95: 무필터 +5.23%p → 필터 후 −0.67%p), "
                       "필터 ON 에서 **널 기준선(풀평균) 대비 Δ** 를 재야 팩터 랭킹이 매매 가능한지 판정된다."),
        "command": CMD116,
        "metric": "factor_money_fillable",
        "check": "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_factor_money_metric_test.py",
        "success": ("사전등록: 멀티팩터 top-k 가 k=3 과 k=5 **둘 다** Δ(바스켓 − 풀평균) ≥ +0.1%p/세션 AND t ≥ 2 "
                    "→ '신호있음'(무작위 k 를 이긴다 = 베타 초과). 미달이면 '노이즈' = 팩터 축도 돈으로 환산되지 않는다"
                    "(모델과 동일 결론 — 다음 레버는 데이터 축뿐). ⚠ 이 판정도 승격 근거가 아니다."),
        "expected": "미지 — 필터 ON 풀평균이 +0.27~+0.52%p/세션(t 0.8~1.5)이라 문턱(+0.1%p·t2)의 의미가 크다.",
        "cost": "실행 30초(읽기 전용)",
        "est_minutes": 3,
        "evidence": ("2026-10-05 16:0x 스모크(비blind): 필터 유지 20,759/54,800행(37.9%) · 풀평균 +0.27~+0.52%p/세션 · "
                     "multifactor top-k Δ 전부 음(−): k3 −0.26(t−0.43) · k5 −0.52(t−1.30) · k10 −0.16 · k20 −0.14. "
                     "단일 k 만 t≥2 인 팩터: value k5 +1.16(t2.17) · momentum k5 +1.91(t1.99) — k 를 고르면 p-해킹이므로 "
                     "사전등록(둘 다)으로만 판정한다."),
        "note": ("metric factor_money_fillable 신설(같은 스크립트의 --fillable 블록, summary_path·parse·judge 3곳 배선, "
                 "per_exp 미생성) + 자체점검 23/23 PASS(신규 8건: fillable 블록 부재 오류·k3/k5 동시 요구·ks 확장 시 노이즈). "
                 "해석 규율: 널 기준선(풀평균) 없는 돈 수치는 무효 · 수수료 왕복 0.21%p 는 Δ 에서 상쇄되어 미적용."),
        "added": f"{NOW} (엔지니어 — CG115 필터 ON 대응, 사전등록)",
        "attempts": [],
    })

with open(BACKLOG, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

# ── 원장: CG114 reported 플래그(다음 틱 중복보고 방지 + 미전달 감지기 짝) ──
sys.path.insert(0, os.path.join(PROJ, "scripts"))
import model_engineer_cycle as m  # noqa: E402

led = m.load_ledger()
n = 0
for r in led:
    if r.get("id") == "CG114" and not r.get("reported"):
        r["reported"] = True
        r["reported_at"] = NOW
        n += 1
if n:
    m._rewrite_ledger(led)

ids_after = {it.get("id") for it in json.load(open(BACKLOG))["items"]}
print(f"backlog: CG114 note 갱신 · CG115 {'등록' if 'CG115' in ids_after else '실패'} · "
      f"CG116 {'등록' if 'CG116' in ids_after else '실패'} (총 {len(ids_after)}항목)")
print(f"ledger: CG114 reported 플래그 {n}건 갱신 (reported_at={NOW})")
print("CG114 결론:", conclusion)
