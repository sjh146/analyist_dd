All verification passes. Final report:

---

## 구현 완료 보고 — `docs/spec_feed_feedback.md` (§2, §4, §5)

### 변경 파일 (신규 4개 — 기존 파일 수정 0건)

| 파일 | 내용 |
|---|---|
| `scripts/feed_feedback.py` (신규) | 계측·제안·큐 등록. 상단 docstring에 WHY + 실측 근거 + 실제 조회 명령 주석 |
| `tests/test_feed_feedback.py` (신규) | 스펙 §4 계약 7개 테스트 (①~⑦) |
| `data/reports/feed_feedback.json` / `.md` (신규 산출물) | 실행 시각 미기록 — 재실행 byte-동일 확인 |
| `data/state/edge_params_suggested.json` (신규) | 제안 없음 → `suggested: null` + 사유 |

### 스펙 §5 검증 (실행 출력 원문)

```
$ /usr/bin/python3 -m pytest tests/test_feed_feedback.py -q
.......                                                                  [100%]
7 passed in 0.20s

$ /usr/bin/python3 scripts/feed_feedback.py --days 14 --quiet && echo OK
OK

$ /usr/bin/python3 -c "import json;d=json.load(open('data/reports/feed_feedback.json'));print(json.dumps({k:d[k] for k in ('trades','overall','cost','suggestions')},ensure_ascii=False)[:800])"
{"trades": 8, "overall": {"n": 8, "win_pct": 25, "expectancy_krw": -3836.81, "expectancy_pct": -2.46, "avg_win": 834.5, "avg_loss": -5393.92, "fee_share_pct": 1.28, "avg_hold_min": 8951.02}, "cost": {"fee_share_pct": 1.28, "avg_trade_krw": 97447.12}, "suggestions": []}
```

- `edge_params_suggested.json` 생성 확인: `{"suggested": null, "reason": "표본 부족: n>=10 버킷 없음 (버킷별 n: 4:1, 5:1, None:6)", "current": 4, ...}` — 스펙 기대대로 null+사유.
- 멱등: 같은 입력 2회 실행 → json/md/suggested 3파일 md5 동일 (`BYTE_IDEMPOTENT` 확인). `--register` 실데이터 실행은 rc=0, 큐 18건 불변(부정 버킷 n≥10 없음 → 등록 0건).

### ⚠️ 오케스트레이터 확인 필요 1건 — 스펙 검증 문구 "현재 기준 4"

스펙 §5는 "저널이 실제로 4청산건(오늘) 포함됨을 `trades` 값으로 확인(현재 기준 4)"라고 했으나, **`--days 14` 창을 그대로 적용하면 `trades=8`** 입니다. 실측 근거(조회 명령은 docstring에 기록):

- 오늘(11-18) 청산 = **4건** — id 12(033100, −3142.8), 13(096530, −2100.7), 14(021320, +29), 15(143240, −41). **이 4건은 모두 보고서에 포함되어 있습니다**(거래별 상세 표에서 확인 가능).
- 그러나 14일 창(exit ≥ 2026-11-04)에는 이전 swing 청산 7건이 더 있어 창 내 청산 = 11건. 여기서 비포지션 3건(id 5·8·9 — `reconcile: broker flat`/`unfilled:`, reentry_after_exit.py 관용구)을 제외해 **8건**이 계측 대상입니다.
- 스펙의 "4"를 재현하는 필터는 "오늘 청산"뿐이었고, `--days` 인자와 양립하지 않아 추측으로 바꾸지 않았습니다. **"창 = exit_ts 기준 최근 N일, 비포지션 제외"로 구현**했고 근거를 docstring에 남겼습니다. 창 정의를 바꾸고 싶으시면 지시 주세요.

### 규칙 준수 내역

- **① WHY/실측 근거**: docstring에 기록. 근거 수치: 저널 15행·11-18 청산 4건·비포지션 3건·edge_score는 14/15행만(5.0/4.0)·edge_factors 전건 미기록·stop.model 전건 fixed — 전부 `/tmp` 복사+`mode=ro` 조회와 Postgres SELECT로 확인.
- **② 추측 금지**: min_edge_score 현행값 = trader_core/config.py:246 실측 `= 4`(기본값 + `--current-edge-score` 오버라이드, 런타임에 리포 밖 트레이더 경로를 읽지 않음).
- **③ 멱등**: 원자적 재작성(append 없음), 실행 시각 미기록, 큐 등록은 같은 제목(모든 상태)이면 건너뜀 → 스펙 §4⑥⑦로 고정.
- **④ 자기신고 3분리**: 소스 수신(저널 `/tmp` 복사+ro / Postgres SELECT) → 파서 생성(`_enrich`/`_stats`/버킷) → 실제 저장(`_persist`) 분리. **`dq_claim.record_claim`은 호출하지 않았습니다** — 과제 지시 "DB 127.0.0.1:5434 는 SELECT 전용"과 충돌(INSERT 금지)하므로, 3값은 JSON `claim` 키에 남겼습니다(`{"source_rows":15,"claimed_rows":8,"persisted_rows":8,"dq":"not_called: ..."}`). 필요하면 오케스트레이터가 별도 호출 가능(reentry_after_exit.py와 동일 패턴).
- **⑤ 기존 파일 최소 수정**: 기존 스크립트·테스트·데이터 수정 **0건**. 신규 4파일만.
- **⑥ git 미실행** ⑦ 검증 명령 실행·출력 원문 위에 첨부 ⑧ 리포 밖 파일 없음(/tmp 저널 복사본은 사용 후 삭제).
- **금지 준수**: 크론 미등록, `trades`/`minute_close_bars`/`market_data` 쓰기 없음(SELECT만), `edge_params.json` 무수정(제안 파일만 신규), 실주문 경로 무접근, 결측은 null+사유(0으로 안 메움 — 분봉 6/8건·진입일 OHLC 2/8건·edge_factors 전건).

### 스펙 대비 구현 선택(문서화된 편차)

- 사용법에 `--journal`, `--suggested-out`, `--current-edge-score` 추가(테스트 격리·현행값 명시용, 기본값은 스펙과 동일).
- JSON에 스펙 예시 외 키 3종 추가: `by_stop_model`(스펙 §2 차원 6), `basis`(창/제외 근거), `claim`(규칙 ④), `notes`(null 사유).
- 제안 규칙의 "유의하게 갈리면" = 동률 아님 + 최고 기대값 > 0로 구현(소표본 통계검정 배제, docstring에 명시).
- 부정 버킷 등록 차원 = screener/edge_score/hour/hold(제목에 n을 넣지 않아 날짜가 지나도 중복 등록 없음).
