# 스펙 — 피드측 엣지 점수 계산 (analyist_dd)

작성 2026-11-17. **사용자 지시**: "내가 말한대로 트레이더 스케일업 해줘"
(엣지 = 하나의 지표가 아니라 확률을 쌓는 것 — 추세·지지저항·캔들·거래량·변동성·과열 회피를 결합해
거래 품질을 필터링한다). 이 스펙은 그 점수를 **피드에서 계산해 실어 보내는** 부분이다.
(트레이더측 게이트·비대칭 청산 배선은 별도 스펙 `spec_trader_edge_gate.md`.)

## 선행 산출물 (있으면 그것을 정본으로 따른다)

`data/reports/edge_stack_backtest.json` — 같은 7요인 정의의 백테스트(2026-04~11 신호).
- 있으면: `recommendation.edge_threshold`, `recommendation.stop`, `recommendation.tp` 등을 **읽어서**
  `data/state/edge_params.json` 에 기록한다(하드코딩 금지).
- 없으면: 요인 정의만 구현하고 `edge_params.json` 은 `{"threshold": null, "source": "backtest_missing"}`.

## 1. `scripts/edge_stack.py` (신규, 순수 함수 — DB·네트워크 없음)

```python
FACTORS = ("trend_dir", "trend_slope", "sr_breakout", "candle_quality",
           "volume_confirm", "vol_fit", "overheat_avoid")

def compute_edge(bars) -> dict:
    """bars: 날짜 오름차순 list[dict(open,high,low,close,volume,trading_value)] (≥30봉).
    반환: {"edge_score": int(0..7), "edge_factors": {name: bool}, "atr_pct": float|None,
           "ma20": float|None, "hhv20": float|None, "edge_reason": str}"""
```
요인 정의(신호일 t 시점 정보만, 미래 정보 금지 — 백테스트와 **동일 정의**):
| 요인 | 정의 |
|---|---|
| trend_dir | `close > MA20` and `MA5 > MA20` |
| trend_slope | `MA20_t > MA20_{t-5}` |
| sr_breakout | `close >= 0.98*HHV20` or `close > HHV20` of t-1 |
| candle_quality | `body/range >= 0.5` and `(close-low)/(high-low) >= 0.667` |
| volume_confirm | `volume/MA20(volume) >= 2.0` |
| vol_fit | `0.01 <= ATR14/close <= 0.06` |
| overheat_avoid | `chg_t <= 12%` and `5일 누적 <= 25%` |
- 봉이 30개 미만이거나 high==low 같은 퇴화 케이스: `edge_score=None`, `edge_reason="no_history"/"degenerate"`.
- 단위·반올림: `atr_pct` 는 비율(0.023 = 2.3%) 소수 6자리. 점수는 int.
- 테스트 가능한 순수 함수여야 한다(입력 bars 만으로 결정, 실행 시각·랜덤 미사용).

## 2. `scripts/feed_export.py` — 발행 시 엣지 필드 부착 (기존 계약 보존)

- CLI 추가: `--edge-score / --no-edge-score` (**기본 on**).
- 각 후보(close/swing/daytrading 전부)에 대해 `market_data` 최근 40거래일을 **한 번의 배치 쿼리**로 읽어
  `compute_edge()` 를 적용하고, item 에 아래 키를 **추가**한다(기존 키 삭제·이름변경 금지):
  ```json
  "edge_score": 5, "edge_factors": {"trend_dir": true, ...}, "atr_pct": 0.0231, "edge_reason": "ok"
  ```
- 이력 부족·DB 실패 시: `edge_score: null, edge_reason: "no_history"|"db_error"` 로 **fail-open**(발행은 계속).
  발행 요약에 `edge_scored=N/M` 을 남긴다.
- `--dry-run` 에서도 계산·출력하고 파일은 쓰지 않는다.
- **기존 소비자 계약 불변**: `contract_check.py` ALL PASS 유지, close/swing 키 값 불변(엣지 키 추가만).

## 3. `data/state/edge_params.json` (신규, 배선용 파라미터)

백테스트 `recommendation` 에서 뽑아 기록:
```json
{"threshold": 5, "stop_mode": "atr", "atr_stop_mult": 1.5, "atr_tp_mult": 3.0,
 "min_rr": 2.0, "source": "data/reports/edge_stack_backtest.json", "measured": {...요약...}}
```
백테스트가 없으면 `threshold: null` + `source: "backtest_missing"`.

## 4. 테스트 — `tests/test_edge_stack.py` (신규 8개)

① 7요인 각각 참/거짓 합성 봉 ② 점수 합(0..7) ③ 미래정보 미사용(마지막 봉을 바꿔도 이전 봉 점수 불변)
④ 30봉 미만 → None+no_history ⑤ high==low 퇴화 ⑥ ATR 클램프·소수 자리 ⑦ `vol_fit` 경계값(1.0%/6.0%)
⑧ `overheat_avoid` 경계(12.0%/25.0%). **기존 테스트 기대값 수정 금지.**

## 5. 검증 (실행 출력 원문 첨부)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_edge_stack.py -q
/usr/bin/python3 scripts/feed_export.py --dry-run 2>&1 | tail -5      # edge_scored=N/M 확인
/usr/bin/python3 scripts/feed_export.py                                 # 실제 발행
/usr/bin/python3 -c "import json;d=json.load(open('data/feed/screener_latest.json'));
print({k:len(v.get('items') or []) for k,v in d['candidates'].items()});
it=(d['candidates']['swing'].get('items') or [None])[0];print({k:it.get(k) for k in ('stock_code','edge_score','atr_pct','edge_reason')})"
/usr/bin/python3 scripts/contract_check.py | tail -3
```
기대: 테스트 전부 통과 · `edge_scored` 가 후보 수와 일치(이력 있는 종목 기준) · 모든 item 에 `edge_score` 키 존재
(null 허용) · `contract_check` **ALL PASS**.

## 6. 금지

- `git` 금지(커밋은 오케스트레이터). DB 는 `market_data` SELECT 만(쓰기 금지), `.env` 값 출력 금지.
- 기존 피드 키 삭제·의미 변경 금지. `score`/`score_kind` 규약 변경 금지.
- 트레이더 코드(`trader-agent/**`) 접근 금지 — 별도 스펙.
- 요인 정의를 백테스트와 다르게 바꾸지 말 것(정의 변경은 새 버전 이름으로만).
