import json, datetime

p = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(p))
items = b['items']
by = {i['id']: i for i in items}

# ── CG204: 원인 규명 + 축 종결(CG205) 기록
cg = by['CG204']
cg['root_cause'] = (
    "측정 전 실패 — exclude_names 29개가 아무 컬럼도 제외하지 않았다(wf_label_sweep.py L2059). "
    "원인(같은 날 코드 주석 L1199-1206 실측): 종목상수 21개는 전부 CORE_FEATURES(48) **밖**이고, "
    "core48 안의 종목상수 3개(sentiment_avg·_5d·_20d)는 std=0 이라 std 필터에서 이미 탈락한다 → "
    "게이트 ON 경로(core_only=True)에서는 제외할 컬럼이 후보에 0개 = 이 패널에서 **구조적으로 측정 불가**.")
cg['closed_note'] = (
    "축 종결 — 게이트 OFF 경로는 CG205 가 같은 날 실측: 짝 Δ 평균 −0.0099±0.0008(앵커 3개, 부호일관 3/3, "
    "폴드 문턱통과 0/3) = 종목상수를 빼면 오히려 나빠진다. '종목상수 선별 지배'는 게이트 ON 배포 경로에서 "
    "성립하지 않고, 게이트 OFF 에서도 제거가 이득이 아니다 → 조정 축(선별·변환·HP·라벨) 종결 근거.")
cg['finished_at'] = cg.get('finished_at') or '2026-11-12T17:04:30+09:00'

# ── CG159: 2026-11-12 실측 근거 추가
c9 = by['CG159']
c9['evidence_2026_11_12'] = {
    "publish_dates": 20, "date_range": "2026-09-22~2026-11-12",
    "constant_block": "370행(8.51%) 단일값 0.5689 — 2026-11-12 발행 4,349종목 기준",
    "block_composition_2026_11_12": "369 ETN + 1 ETF (상수블록 전량이 ETF/ETN)",
    "selected_ge_0_55_2026_11_12": 550,
    "selected_by_instrument_2026_11_12": {"ETN": 369, "STOCK": 109, "ETF": 72},
    "selected_without_price_on_that_date": 403,
    "block_share_of_selected_pct_mean": 55.96,
    "note": ("0.55 문턱 통과 후보의 평균 56%(2026-11-12 67%)가 상수블록이고, 그 중 가격 커버리지가 있는 행은 "
             "147/550 뿐. 순수익 비교는 체결가능 블록행이 0~2행이라 **판정 불가** — 보고하지 않는다."),
    "probe": "docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg207_block_evidence.py'",
}

new = {
    "id": "CG207",
    "title": ("[돈 경로·발행계약] 절대문턱 0.55 를 통과하는 후보의 절반 이상이 상수블록(ETF/ETN) — "
              "그 행들은 가격 커버리지조차 없다"),
    "status": "done", "priority": "1", "kind": "measurement", "owner": "engineer",
    "affects_model": "False",
    "metric": "block_money_evidence",
    "summary_out": "/app/reports/overnight/cg207_block_evidence.json",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg207_block_evidence.py'",
    "counterfactual": ("현행 발행 경로: get_all_stocks()=4,349종목(ETF/ETN 포함) → ml_predictions 그대로 적재 → "
                       "트레이더 절대문턱 0.55 소비"),
    "success": ("① 문턱 통과 후보 중 상수블록 비중 ≥ 30% 이면 '선택 후보 오염'으로 기록 ② 블록행의 체결가능 표본 "
                "≥ 30 이면 블록 vs 비블록 순수익 비교를 보고, 미달이면 '판정불가(표본부족)'로 적고 수익 주장 금지"),
    "hypothesis": ("CG159 는 발행측 사실(370행 상수블록)만 남겨 승인 판단에 돈 근거가 없었다. 문턱 소비 경로에서 "
                   "그 블록이 실제로 후보를 잠식하는지, 잠식한다면 실현수익이 얼마나 다른지를 같은 데이터로 잰다."),
    "expected": "이득은 AUC 가 아니라 발행 계약 결정의 근거 — 상수블록 제외(PREDICT_EXCLUDE_ETFETN=1) 승인의 정량 근거.",
    "cost": "저 (읽기 전용 SELECT, ~40초)",
    "est_minutes": "2",
    "created_at": "2026-11-12T21:00:00+09:00",
    "finished_at": "2026-11-12T21:05:00+09:00",
    "notes": ("2026-11-12 21:0x 엔지니어 자율(장외 틱). 저녁 재학습이 CPU 를 쓰는 중이라 신규 학습은 띄우지 않고 "
              "읽기 전용 측정만 수행. 신규 코드 scripts/_cg207_block_evidence.py(읽기 전용·판정선 없음)."),
    "attempts": [{"ts": "2026-11-12T21:05:00+09:00", "rc": 0, "verdict": "신호있음", "elapsed_min": 1}],
    "result": {
        "verdict": "신호있음(선택 후보 오염) — 수익 비교는 판정불가(표본부족)",
        "detail": ("기간 2026-09-22~11-12(20발행일): 0.55 문턱 통과 후보 중 상수블록 평균 비중 55.96%. "
                   "2026-11-12: 발행 4,349 · 선택 550(ETN 369·ETF 72·STOCK 109) · 상수블록 370 = 67.27%. "
                   "선택 550 중 당일 가격행이 없는 종목 403(73%) — 가격테이블로 체결가·수익 계산 자체 불가. "
                   "블록 체결가능 표본 0~2행 → 블록 vs 비블록 순수익 비교는 **보고하지 않는다**."),
        "delta": None, "per_exp": None, "rc": 0,
    },
}

if 'CG207' not in by:
    items.insert(0, new)
b['updated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
json.dump(b, open(p, 'w'), ensure_ascii=False, indent=1)
print('ok items=', len(items), 'CG204 keys+', 'root_cause' in by['CG204'],
      'CG159 evidence', 'evidence_2026_11_12' in by['CG159'], 'CG207', 'CG207' in by)
