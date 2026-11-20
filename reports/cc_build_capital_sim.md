All verifications pass. The implementation is complete and now internally consistent. Final report:

---

# 자본 제약 시뮬레이터 v2 구현 보고 (`docs/spec_capital_sim.md`)

## 산출물 (신규 4개, 기존 파일 수정 0)

| 파일 | 내용 |
|---|---|
| `scripts/capital_sim.py` | 시뮬레이터 본체 (~900줄). v1 재사용은 import만: `esb.load`/`esb.compute_features`/`edge_stack.compute_edge` |
| `tests/test_capital_sim.py` | 스펙 계약 8개 (DB 없이 합성 데이터, 손계산 대조) |
| `data/reports/capital_sim.json` / `.md` | 산출물 — 실행 시각 미기록, sort_keys 고정, 멱등 |

**기존 파일: 하나도 수정 안 함** (edge_stack.py·edge_stack_backtest.py·feed_export.py·기존 테스트 모두 무변경). git 명령 0회.

## 규칙 준수

- **① WHY+실측**: 모듈 docstring에 v1의 3가지 한계(자본 무시·동시보유 무시·MaxDD 측정 불가)와 DB 조회 근거 기록 — `market_data` 2023-01-02~2026-11-17 총 3,227,630행·3,980종목, 시뮬레이션 윈도우 548,792행, 096530 11-16 종가 24,200원 → 3주(스펙 인용값 교차확인), v1 JSON 원본 신호 14,559건.
- **② 추측 금지**: 모든 상수·분할·근거는 조회값. 조회 명령은 주석에.
- **③ 멱등**: 타임스탬프 없음 → 재실행 시 바이트 동일 (아래 md5 확인).
- **④ 자기신고**: JSON에 `claim` 내장 — source_rows(market_data_fetched 548,792 / signals_matched 14,555) / parsed_rows(시뮬레이션 16회 + 워크포워드 32회 등) / persisted_rows 3분리. `dq_claim.record_claim`은 DB SELECT 전용이라 미사용(그 사유를 claim.note에 명시 — v1 선례).
- **⑤⑥⑧**: 기존 파일 무수정, git 미실행, 리포 밖 파일 0.
- **금지 준수**: DB SELECT-only, 비밀번호 무출력, 실주문·브리지·트레이더 무접근, 임계값 사후 변경 없음.

## 구현 중 발견·수정한 버그 1건 (사후 임계값 변경 아님 — 측정 정의 버그)

최종 검수에서 **`ambiguous_pct`(애매일 비율)가 95.9%** 로 나오는 것을 발견했습니다. v1의 같은 정의(당일 both-touch)가 38.2%인데 2.5배라 의심되어 검증했습니다:

- 원인: worst/best 간 **손익 KRW를 비교**하고 있었는데, 두 모드는 자본 경로가 달라 같은 신호라도 수량이 다르고 → 같은 청산 경로여도 손익 KRW가 달라 전부 '애매'로 집계됨.
- 실측 대조(조회 명령 주석으로 기록): 엔진이 실제 진입한 460건(가격대 내 + 일 3건)의 both-touch 실측치는 **36.3%** — 엔진 봉 조회는 정상, 지표 정의만 오류.
- 수정: **청산 경로(사유·가격 leg, 수량 제외) 비교**로 변경. 한쪽 모드에만 있는 거래(자본 경로 차이)는 분모에서 제외 — 체결집합 차이는 skipped_* 항목으로 이미 보고됨.
- 교차검증 통과: `fixed_15_45` 애매 24.2% ≈ **v1의 (1.5%,4.5%) 셀 25.03%**, `fixed_1_3` 36.6% ≈ **v1의 38.2%**(전체 신호 vs 자본제약 진입 부분집합 차이) — 정의 복원 확인. PnL·EV·MaxDD·추천 결과는 전혀 안 바뀜.
- 부수 정리: `avg_hold_sessions`가 진입봉 장중청산 0 / 진입봉 종가청산 1로 뒤섞이던 것 → 세션을 봉 참여 시점에 증가시켜 same_day 전부 정확히 1.0으로 통일(타임스탑 트리거는 동일).

## 결과 요약 (전체 기간 2026-04-01~11-14, worst=보수)

| 규칙 | 프로파일 | n | 기대값%/건 | 총손익 | MaxDD% | 애매% |
|---|---|---|---|---|---|---|
| fixed_1_3 | same_day | 460 | −0.74 | −178,399원 | 52.99 | 36.6 |
| fixed_1_3 | swing5 | 457 | −0.74 | −178,634원 | 53.00 | 36.5 |
| fixed_15_45 | same_day | 460 | −0.80 | −184,895원 | 57.70 | 24.2 |
| fixed_15_45 | swing5 | 431 | −0.82 | −179,381원 | 55.16 | 24.2 |
| partial_1r | same_day | 460 | −0.83 | −188,330원 | 57.86 | 61.8 |
| partial_1r | swing5 | 457 | −0.83 | −188,514원 | 57.97 | 62.6 |
| trail_atr | same_day | 460 | −0.62 | −163,542원 | 48.77 | 66.5 |
| trail_atr | swing5 | 447 | **−0.50** | **−143,953원** | **40.23** | 68.6 |

- **진입 제약의 실체**: 신호 14,555건 중 실제 진입은 ~460건(3%). 미진입 원인: 슬롯초과 11,692건(80%) ≫ 가격대 2,397건 ≫ 자본 0~10건. 스펙 WHY #2의 "못 들어간 신호"가 정량화됨.
- **추천 없음** (억지 추천 금지 준수): 8조합 전부 홀드아웃 worst 기대값 음수. 선택 구간 최고 trail_atr/swing5(선택 EV −0.35%)도 홀드아웃 조건 ①/②/④ 미충족.
- best(낙관) 밴드는 참고: trail_atr/swing5 best EV +1.24%/건 — worst/best 격차 = 일봉 순서 불확실성 그 자체.

## 검증 명령 실행 출력 원문 (스펙 §4)

**pytest**:
```
$ /usr/bin/python3 -m pytest tests/test_capital_sim.py -q
........                                                                 [100%]
8 passed in 0.52s
```

**본 실행** (재실행 포함 — 멱등 확인):
```
$ /usr/bin/python3 scripts/capital_sim.py --quiet; echo "script_exit=$?"
script_exit=0
$ md5sum data/reports/capital_sim.json data/reports/capital_sim.md > /tmp/cap_sim_before.md5
$ /usr/bin/python3 scripts/capital_sim.py --quiet   # 재실행
$ md5sum -c /tmp/cap_sim_before.md5
data/reports/capital_sim.json: OK
data/reports/capital_sim.md: OK
```

**스펙의 검사 스니펫**:
```
{"conditions": {"c1_holdout_ev_gt_010": false, "c2_holdout_pnl_gt_0": false, "c3_holdout_n_ge_100": true, "c4_holdout_maxdd_le_15": false}, "found": false, ...}
fixed_1_3 same_day EV -0.74 PnL -178399.05 MaxDD 52.9875 n 460
fixed_1_3 swing5 EV -0.7439 PnL -178634.2 MaxDD 53.0035 n 457
fixed_15_45 same_day EV -0.8037 PnL -184894.67 MaxDD 57.7032 n 460
fixed_15_45 swing5 EV -0.8163 PnL -179380.55 MaxDD 55.157 n 431
partial_1r same_day EV -0.8287 PnL -188330.08 MaxDD 57.8632 n 460
partial_1r swing5 EV -0.8333 PnL -188513.57 MaxDD 57.9733 n 457
trail_atr same_day EV -0.6225 PnL -163541.72 MaxDD 48.7678 n 460
trail_atr swing5 EV -0.4972 PnL -143952.92 MaxDD 40.2298 n 447
```

## 한계 (MD §6에 기재)

일봉 순서 불확실성(worst/best 밴드) · 슬리피지 0 가정 · 전량 체결 가정 · 생존편향 · 강제청산의 종가 프록시 · MaxDD는 일별 마감 곡선(장중 미반영) · 홀드아웃 1회 · 부분익절 floor(qty/2) · 트레일 스탑 초기손절 하한 유지.

커밋은 하지 않았습니다(오케스트레이터 담당).
