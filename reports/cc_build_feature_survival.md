완료. 스펙 `docs/spec_feature_survival.md` 계약대로 구현하고 검증까지 마쳤다.

# 구현 보고 — 피처 생존 판정기 (3축: 분산·커버리지·누수)

## 신규 파일 (4개 — 스펙에 열거된 것만 생성)

| 파일 | 내용 |
|---|---|
| `scripts/feature_survival_report.py` | 판정기 본체 (numpy/pandas만, DB·네트워크 무접근) |
| `tests/test_feature_survival.py` | 신규 테스트 7개 |
| `data/reports/feature_survival.json` | 실행 산출물 (162,225 bytes) |
| `data/reports/feature_survival.md` | 사람용 요약 (5,723 bytes) |

**기존 파일 수정: 없음.** 패널 npz는 읽기만, `r13_dead_feature_audit.json`도 읽기만. git 명령 미실행.

## 규칙 준수 내역

- **① WHY + 실측 근거**: 스크립트 docstring에 기록 — alive/dead 76/97→16/3 뒤집힘 전례(`docs/QUANT_FINDINGS.md:54,139`에서 실측 확인), 계기판 공백. **② 조회 명령 주석**: 패널 키/shape/중복(0건)/NaN(1,398,300)/r13 dead_total(35) 조회 명령을 docstring에 원문으로 남김. **③ 멱등**: `generated_at` 없음, 같은 패널 → 같은 바이트(md5 2회 동일 확인). **④ 자기신고 3분리**: `claim` 키에 source_rows=54800 / parsed_features=213 / persisted_features=213(JSON 재독해 대조). `dq_claim.record_claim`은 psycopg2+DB 필요 → 스펙 "DB 접근 금지"와 충돌해 **재사용 불가**하므로 파일 내 자기신고로 대체(사유를 코드·MD에 명시).

## 검증 출력 원문 (스펙 '검증' 명령 그대로)

```
$ /usr/bin/python3 -m pytest tests/test_feature_survival.py -q
.......                                                                  [100%]
7 passed in 0.61s

$ /usr/bin/python3 scripts/feature_survival_report.py --quiet && echo OK
OK

$ /usr/bin/python3 -c "..." (panel/summary 출력)
{"shape": [54800, 213], "features": 213, "dates": 279, "span": ["2025-08-04", "2026-09-23"], "codes": 200, "path": "/home/jhshi/analyist_dd/services/xgboost-ml/app/models/wf/panel_prod200.npz", "mtime": 1790965521.2047262}
{"total": 213, "alive": 117, "dead": 69, "conditional": 27, "by_class": {"DEAD_ALL_NULL": 0, "DEAD_CONST_ZERO": 24, "DEAD_CONST_OTHER": 0, "DEAD_SPARSE": 45, "CONST_MARKET": 24, "CONST_STOCK": 0, "LEAK_SUSPECT": 3, "ALIVE": 117}, "alive_and_dead": {...}}
features: 213 | classes: ['ALIVE', 'CONST_MARKET', 'DEAD_CONST_ZERO', 'DEAD_SPARSE', 'LEAK_SUSPECT']

$ md5sum data/reports/feature_survival.json
24c9d5ea1372d5108f72cb75df9390de  data/reports/feature_survival.json
$ /usr/bin/python3 scripts/feature_survival_report.py --quiet && md5sum data/reports/feature_survival.json
24c9d5ea1372d5108f72cb75df9390de  data/reports/feature_survival.json   ← 멱등: 두 md5 동일
```

계약 확인: `panel.shape == [54800, 213]` ✓ · `len(features) == 213` ✓ · 모든 class가 8종 내 ✓ · md5 동일 ✓.

## LEAK_SUSPECT — 3건 (이름 명시)

| 피처 | 근거 |
|---|---|
| `price_vs_sector` | L2 \|corr\|=1.000000 (당일 수익률과 사실상 동일) |
| `return_1d` | L2 \|corr\|=1.000000 |
| `relative_strength` | L2 \|corr\|=0.987334 |

셋 다 L2(당일 수익률 동시상관)로 걸렸다. MD 한계 절에 "의심 표시일 뿐, 생성 코드를 봐야 확정" 명시.

## 주목할 실측 (판정 결과, 임계값 사후 조정 없음)

- dead 69 = CONST_ZERO 24(전부 0) + SPARSE 45 — r13의 dead_total 35와는 정의·창이 달라 차이(교차표 출력). r13에서 죽었는데 이번엔 부활: 19개(`theme_count` 등 source_absent 2개가 ALIVE로), 이번에 새로 죽음: 20개(`event_*_5d` 시리즈, `kalman_*` — nnz 1.1% 수준의 희소).
- `redundant_pairs` 20쌍 전부 corr=1.0 — 패널에 **중복 컬럼이 실제로 존재**(`cross_trend` 2개, `price_vs_sector`≡`return_1d` 등). pandas와 전수 대조해 정확함 확인.
- 계기판 정의(결측 0 치환 nonzero>0)를 같은 패널에 적용하면 alive=189/dead=24 — 이 판정(117/69)과 차이가 나는 이유를 summary.note·MD 한계 절에 실었다.

## 스펙과의 판단/편차 3가지 (사실대로)

1. **테스트 ④** "10% 값 → DEAD_SPARSE"는 스펙 기본 `--min-coverage 0.05`로는 성립 불가(0.10 ≥ 0.05). CLI 기본값은 스펙 그대로 두고, 테스트 호출에서 `min_coverage=0.2`를 주어 스펙 기대를 충족(임계값 사후 조정 아님 — 테스트 docstring에 명시).
2. **"신규 7개" vs ①~⑨**: ⑦⑧을 한 테스트로 병합(스펙 허용)하고 ⑨(멱등)도 같은 테스트에 병합해 정확히 7개 함수로 맞췄다.
3. **quant_scoreboard.json**: 스펙 배경의 "feature_alive_count n/a"는 현행 파일(ts 2026-11-16)에 **키 자체가 없음**을 실측 확인 — "공백이 여전히 존재"로 정정해 기록했다(스펙 지시대로 이 파일이 공백을 메운다).

## 구현 중 잡은 버그 2건 (수정 완료)

- `_redundant_pairs` 첫 버전이 열별 전역 mean/std로 표준화 후 내적 → corr=1.412라는 **불가능한 값** 산출. pairwise Pearson 정확 공식 `(n·Σxy−Σx·Σy)/√((n·Σx²−(Σx)²)(n·Σy²−(Σy)²))`으로 교체, pandas와 교차 검증 완료.
- 누수 판정에서 x 분산 0일 때 corr=None을 `%.6f`로 포맷하던 TypeError 수정.
