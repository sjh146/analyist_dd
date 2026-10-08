"""일회성: CG142(학습 유니버스 동결) 등록 + CG141 선행조건 정정 + CG73 스크린 갱신. idempotent."""
import json

P = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(P))
items = d['items']
by = {i.get('id'): i for i in items}
assert len(by) == len(items), 'duplicate ids!'

# --- CG141 선행조건 정정 -------------------------------------------------
g141 = by['CG141']
add = (" ⓪ (**신규 선행, 2026-10-09 실측**) **유니버스 동결** — 목표 집합 자체가 매일 바뀐다: "
       "select_training_universe 의 창 시작이 now−60 이라 적격 풀이 5종목만 달라져도(2652 vs 2657) 같은 seed=0 "
       "선택 200종목 교집합이 26/200(=87% 교체)이 된다(data/reports/universe_stability_probe_20261009.txt). "
       "지금 상태에서 수집 목록을 맞춰도 다음 날 87%가 어긋난다 → 먼저 CG142 로 동결한 뒤 그 목록에 맞춘다.")
if '⓪ (**신규 선행' not in (g141.get('setup_needed') or ''):
    g141['setup_needed'] = (g141.get('setup_needed') or '') + ' ' + add
    print('CG141 setup_needed updated')
else:
    print('CG141 already updated')

# --- CG73 전수 스크린 갱신 ----------------------------------------------
g73 = by['CG73']
add73 = (" | 2026-10-09 재측정(전수 스크린, scripts/_data_axis_scan.py): DB 테이블 39개를 패널(panel_prod200 "
         "200종목·2025-08-04~2026-09-23)과 대조 — 패널을 100% 덮는 원천은 market_data/stock_prices·"
         "supply_market_features·financial_ratio_features(=이미 사용)뿐이고, event_features 191 · disclosures 191 는 "
         "단변량 edge 0 으로 종결됨. 나머지는 전부 부분 커버(foreign_institutional 87/200 무정보 · sns_posts 22 · "
         "news_events 15 · krx_short_selling 11 · minute_bars 23·15:01~15:30) 또는 시장레벨. "
         "→ '패널 구간을 덮는 미사용 PIT 원천 없음' 결론 유지. 재개는 수집 확장뿐.")
if '2026-10-09 재측정(전수 스크린' not in (g73.get('note') or ''):
    g73['note'] = (g73.get('note') or '') + add73
    print('CG73 note updated')
else:
    print('CG73 already updated')

# --- CG142 신규 등록 -----------------------------------------------------
if 'CG142' in by:
    print('CG142 exists — skip')
else:
    new = {
        "id": "CG142", "status": "needs_setup", "priority": 1,
        "title": "[측정정합성·승인] 학습 유니버스 동결 — 재학습 표본이 매일 87% 재추첨된다(창 시작 = now−60)",
        "hypothesis": (
            "select_training_universe 의 적격 창 시작 기본값이 `datetime.now()−60일` 이라 날짜가 바뀌면 창이 "
            "미끄러진다. 적격 풀이 5종목(0.19%)만 달라져도 seed 셔플의 **입력 순서**가 바뀌어 선택 200종목이 "
            "통째로 재추첨된다 → (a) 프로덕션 재학습 표본이 매일 ~87% 교체되고(고정 end-date 패널과는 31/200 "
            "만 일치), (b) '같은 유니버스' 를 전제한 모델 비교가 표본 교체와 뒤섞여 사전문턱 +0.02 판정의 "
            "신뢰구간이 부풀려진다. 창을 고정하면 같은 유니버스가 재현되어 비교가 표본 교체와 분리된다."),
        "counterfactual": "현행(UNIVERSE_ASOF_DATE 미설정 = now−60) 유니버스",
        "success": ("① UNIVERSE_ASOF_DATE 지정 시 서로 다른 프로세스·서로 다른 실행일에서 유니버스가 100% 동일 "
                    "② 미설정 시 종전과 비트 동일(무회귀) ③ 동결 후 재학습 2회의 학습 표본 교집합 100% "
                    "④ 자체점검 scripts/_universe_pin_test.py 전부 PASS"),
        "command": "UNIVERSE_ASOF_DATE=2026-10-08 docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_universe_pin_test.py",
        "metric": "champion_robust_eval",
        "est_minutes": 10,
        "setup_needed": (
            "동결 수리 코드는 **완료**(services/xgboost-ml/app/training/universe.py::_default_date_from — "
            "UNIVERSE_ASOF_DATE 미설정이면 현행 비트 동일, 자체점검 7/7 PASS). 남은 것은 **운영 결정 = 유니버스 "
            "규칙 변경이라 리뷰보드 승인 대상**: ① .env 에 `UNIVERSE_ASOF_DATE=<고정일>` 추가(또는 재학습 크론 "
            "환경에 export) ② 더 강한 형태로는 고정 유니버스 목록을 산출물(json)로 커밋하고 학습·수집이 그것을 "
            "읽게 한다(CG141 수집 정합의 전제). 승인하면 내가 2줄 배선·회귀·커밋한다."),
        "note": (
            "2026-10-09 엔지니어 자율(장외 루프). 실측: eligible 2652(date_from 2026-08-09) vs 2657(2026-08-03) "
            "= 5종목 차이 → 같은 seed=0 선택 200종목 교집합 **26/200(87% 교체)**. date_from 을 같게 하면 "
            "200/200 완전 동일 → 원인은 날짜 하나뿐. panel_prod200(2026-10-02 빌드, --universe prod --end-date "
            "2026-09-27)과 오늘 기본 유니버스의 교집합은 31/200, 빌드 당시 창(2026-08-03)으로 되돌려도 56/200 "
            "— 즉 **패널 유니버스는 오늘 DB 에서 재현되지 않는다**. 증거·재현: "
            "data/reports/universe_stability_probe_20261009.txt · scripts/_universe_stability_probe.py · "
            "scripts/_cg141_scope_probe.py. 회귀: _universe_determinism_test ALL PASS · _universe_mode_test "
            "전부 PASS · _universe_pin_test 7/7 PASS. ⚠ 컨테이너에 pytest 모듈이 없어 스킬의 pytest 명령은 "
            "실행 불가(대신 위 3개 자체점검으로 검증). 이 항목은 새 성능 레버가 아니라 측정 정합성 수리다."),
        "attempts": [],
    }
    items.append(new)
    print('CG142 added')

d['items'] = items
json.dump(d, open(P, 'w'), ensure_ascii=False, indent=2)

d2 = json.load(open(P))
ids2 = [i['id'] for i in d2['items']]
assert 'CG142' in ids2, 'CG142 not persisted!'
assert len(ids2) == len(set(ids2)), 'dup ids'
n142 = [i for i in d2['items'] if i['id'] == 'CG142'][0]
print('verify: total', len(ids2), '| CG142 status =', n142['status'],
      '| CG141 setup⓪ =', '⓪ (**신규 선행' in by['CG141']['setup_needed'])
