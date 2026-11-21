import json
from datetime import datetime

KST = "+09:00"
now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + KST

p = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(p))
items = d['items'] if isinstance(d, dict) and 'items' in d else d

for i in items:
    if not isinstance(i, dict):
        continue
    if i.get('id') == 'CG391':
        i['status'] = 'done'
        i['result'] = {
            "verdict": "노이즈",
            "detail": (
                "청정 패널 panel_prod200_v6 · 5폴드×5시드 · 같은 런 2-arm(CO_rank_smooth_d1_h5 vs CO_smooth_d1_h5). "
                "AUC: 0.5497±0.0083 vs 0.5487±0.0115 → Δ+0.0010 (rank 우위가 청정 패널에서 사라짐 — "
                "기록상 +0.0227 은 49종목 누수 패널 panel_420_asofpatch 값이었다). "
                "돈(h1/stride1/close_h·수수료 0.21%p·255세션·풀중앙값 75): rank arm k=3 +0.028%p/session(t 0.15) · k=5 +0.025(t 0.14) · "
                "대조 k=3 −0.042/k=5 +0.026 · Δ(rank−대조) k=3 +0.070(t 0.60, 108/103/44) · k=5 −0.002(t −0.02, 122/117/16) · "
                "분할 unstable [+0.165/−0.109](k=3) · 널(풀) 대비 k=3 +0.326(t 2.26) k=5 +0.323(t 2.68). "
                "h5/stride5(비중첩 n=55): Δ k=3 +0.294(t 0.54) k=5 +0.054(t 0.12) — 검정력 없음. "
                "사전등록 ①Δ≥+0.1%p ✗(k=3 +0.070) ②t≥2 ✗(0.60/−0.02) ③both_positive ✗ → 미충족"
            ),
            "money_note": (
                "AUC 로는 +0.0227 이던 rank 축이 청정 패널에서는 AUC +0.0010·돈 Δ+0.070%p(t 0.60) 로 "
                "둘 다 사라진다 → 'AUC 만 오른 후보'도 아니고 '돈도 안 오른 후보'다. "
                "유일하게 남는 것은 널 대비 상대엣지(+0.32%p, t 2.3~2.7)로 rank 유무와 무관하게 "
                "모델 자체가 갖는 값이다."
            ),
        }
        i['note'] = (i.get('note', '') +
                     " | 2026-11-21 22:2x 판정: 노이즈 — rank 축 종결(청정 패널 AUC Δ+0.0010 · "
                     "돈 Δ+0.070%p t 0.60 · h5/stride5 Δ+0.294 t 0.54).")
    if i.get('id') == 'CG345':
        i['status'] = 'backlog'
        i['title'] = "[보류·근거부족] " + str(i.get('title', '')).lstrip('[').split(']', 1)[-1].strip()
        i['note'] = (i.get('note', '') +
                     " | 2026-11-21 22:2x 엔지니어: **착수 보류** — 이 항목의 유일한 목적이 'rank 축을 배포 경로에 올린다'였는데, "
                     "CG391 실측(청정 패널 prod200_v6, 같은 런 2-arm)에서 rank 성분이 AUC Δ+0.0010 · "
                     "돈 Δ(rank−대조) +0.070%p(t 0.60, k=3) / −0.002(t −0.02, k=5) · 분할 unstable 로 "
                     "둘 다 사라졌다. 3시간 구현 + shadow 1주를 쓸 근거가 없다. "
                     "다른 횡단면 피처(당일 업종 상대강도 등)를 배포 경로에 올릴 때 재개한다.")

json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)

# ── 원장 기록(수동 실행 2건) ────────────────────────────────────────────────────
led = 'data/reports/model_engineer_ledger.jsonl'
rows = [
    {"ts": now, "id": "CG390", "rc": 0, "elapsed_min": 0.2, "metric": "fillable_topk_expectancy",
     "manual": True, "verdict": "노이즈",
     "summary": ("dispersion 게이트(점수 std vs 과거 20세션 중앙값) · h1/stride1 · ON 118세션 "
                 "k=5 +0.137%p(t 0.56, 분할 both_positive) k=3 +0.052(t 0.21) · OFF −0.083/−0.131 · "
                 "Δ(ON−OFF) +0.220%p(문턱 +0.5 미달) · ON 풀 −0.432 vs OFF 풀 −0.123 → 국면 예측 실패"),
     "artifacts": ["services/xgboost-ml/reports/overnight/cg390_disp_gate_h1.json",
                   "scripts/_cg390_gate_test.py"]},
    {"ts": now, "id": "CG391", "rc": 0, "elapsed_min": 5.9, "metric": "fillable_topk_expectancy",
     "manual": True, "verdict": "노이즈",
     "summary": ("rank 축 청정 패널 재측정 · AUC 0.5497±0.0083 vs 0.5487±0.0115 (Δ+0.0010) · "
                 "돈 h1/stride1 255세션 Δ(rank−대조) +0.070%p(t 0.60)/−0.002(t −0.02) · "
                 "h5/stride5 n=55 Δ+0.294(t 0.54) → rank 축 종결, CG345 착수 보류"),
     "artifacts": ["services/xgboost-ml/reports/overnight/cg391_rank_both.jsonl",
                   "services/xgboost-ml/reports/overnight/cg391_summary.json",
                   "services/xgboost-ml/reports/overnight/cg391_money_h1.json",
                   "services/xgboost-ml/reports/overnight/cg391_money_h5s5.json"]},
]
with open(led, 'a', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("updated; ledger rows appended:", len(rows))
