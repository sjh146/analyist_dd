# 스펙 — 트레이더측 엣지 게이트 + ATR 비대칭 청산 (실계좌 경로)

작성 2026-11-17. **사용자 지시**: "내가 말한대로 트레이더 스케일업 해줘".
원칙: ① 승률이 아니라 **기대값(확률 × 손익비 − 비용)** 으로 판정 ② **손실은 작게, 이익은 크게**(비대칭 R:R)
③ 엣지는 단일 지표가 아니라 **다요인 합의 점수**로 거래 품질을 필터링.
짝 스펙: `docs/spec_feed_edge_score.md` (피드가 `edge_score`·`edge_factors`·`atr_pct` 를 실어 보낸다).

## 선행 실측 (재측정 금지)

- 실계좌 11건: 승률 22.2% · 평균이익 1,028원 / 평균손실 5,075원(= **손익비 0.203, 역방향**) · 기대값 −2,943원/건.
- 손익분기 승률(왕복 0.21%): ±1% 60.5% · ±1.5% 57.0% · **R:R 3 = 43.8%** · R:R 4 = 40.0%.
  현 신호 무손절 기준선 승률은 36.0% → 진입 필터로 +8%p 를 벌어야 R:R 3 이 성립한다.
- 직전 배선(커밋 `e9dc59a`): daytrading 은 `stop_mode="fixed"`, 1.5%/2.5%. 이 스펙은 그 위에
  `stop_mode="atr"`(비대칭)와 엣지 게이트를 **추가**한다(고정 라인은 롤백·대조군으로 남긴다).

## 변경 대상 (이 5파일만)

1. **`trader-agent/trader_core/feed.py`** — 후보가 `edge_score` / `edge_factors` / `atr_pct` / `edge_reason`
   를 `Candidate.extras` 로 보존하도록 파싱을 확인·보강한다(없으면 `None` — 구버전 피드 호환 필수).
   기존 필드(`score`, `score_kind`, `ml_prob`, `signal_date`, `valid_until`)의 의미·타입 불변.
2. **`trader-agent/trader_core/config.py`** — 필드 3개 추가
   ```python
   min_edge_score: int = 0        # 0 = 비활성. 피드 edge_score 가 이 값 미만이면 진입 차단
   min_rr: float = 0.0            # 0 = 비활성. (tp_pct/stop_pct) < 이 값이면 진입 차단
   edge_gate_fail_open: bool = True  # edge_score 가 None(이력부족·구피드)일 때: True=통과+로그, False=차단
   ```
   각 필드 주석에 WHY(위 실측·손익분기 승률 표)와 근거 스펙 경로를 남긴다. `as_dict()` 자동 포함 확인.
3. **`trader-agent/trader_core/profiles.py`** — ATR 모드 지원
   - 필드 추가: `atr_stop_mult: Optional[float] = None`, `atr_tp_mult: Optional[float] = None`
     (`stop_mode="atr"` 일 때 사용. `stop_mode` 는 이미 있음 — 값 `"vol"|"fixed"|"atr"`).
   - `DEFAULT_PROFILES["daytrading"]` 을 **`stop_mode="atr"`, `atr_stop_mult=1.5`, `atr_tp_mult=3.0`** 로 갱신하고
     기존 `stop_pct`/`tp_pct`/`max_stop_pct` 는 **폴백용으로 유지**(ATR 값이 없거나 퇴화면 고정 라인 사용).
     `swing`/`close`/`rank_momentum` 은 `stop_mode="vol"` 그대로(정책 불변).
4. **`trader-agent/trader_core/engine.py`**
   - 청산 폭 결정부(`_evaluate_candidate` 사이징 블록)에서 `stop_mode` 3분기:
     ```python
     if stop_mode == "atr" and extras["atr_pct"] and prof.atr_stop_mult and prof.atr_tp_mult:
         stop_pct = clamp(extras["atr_pct"] * prof.atr_stop_mult, 0.005, prof.max_stop_pct or 0.08)
         tp_pct   = clamp(extras["atr_pct"] * prof.atr_tp_mult,   0.005, 0.20)
     elif stop_mode == "fixed" and prof.stop_pct and prof.tp_pct:   # 기존(1.5/2.5) 경로
         ...
     else:                                                          # 기존 변동성 경로
         ...
     ```
     (ATR 이 퇴화(0 또는 None)면 fixed → vol 순으로 폴백하고 그 사실을 `algo_json.stop["model"]` 에 남긴다:
     `"atr"|"fixed"|"vol-range"`.)
   - **엣지 게이트**: `daily_trade_count` 다음, `post_exit_cooldown` **앞**에 게이트 2개 추가
     (차단일 때만 `GateResult` append — 기존 `test_engine.py` 의 통과 게이트 6개 고정 기대를 깨지 않기 위해):
     ```python
     edge = candidate.extras.get("edge_score")
     if self.config.min_edge_score > 0:
         if edge is None:
             if not self.config.edge_gate_fail_open: block("edge_score_missing")
             # fail-open 이면 통과 + algo_json 에 edge_gate="fail_open" 기록
         elif int(edge) < self.config.min_edge_score: block("edge_score_low ...")
     if self.config.min_rr > 0 and stop_pct > 0 and (tp_pct/stop_pct) < self.config.min_rr:
         block("rr_too_low (rr=%.2f < %.2f)")
     ```
   - 게이트 이름은 `edge_score` / `rr_min` 로 남기고 사유 문자열에 수치를 포함한다(사후분석 근거).
5. **`trader-agent/presets/edge_gate.json`** (신규) — 스위치·파라미터를 한 파일로:
   ```json
   {"_comment": "엣지 게이트/ATR 라인 스위치. 값은 data/state/edge_params.json(백테스트 추천)에서 온다.",
    "min_edge_score": 5, "min_rr": 2.0,
    "screener_profiles": {"daytrading": {"stop_mode": "atr", "atr_stop_mult": 1.5, "atr_tp_mult": 3.0}}}
   ```
   (Config 는 `screener_profiles` 를 받지 않으므로 실제 프로파일 정본은 `profiles.py` — 프리셋 `_comment` 에 명시.)

## 테스트 — `trader-agent/trader_core/tests/test_edge_gate.py` (신규 6개)

① `edge_score=3 < min_edge_score=5` → 차단, 사유에 수치 ② `edge_score=6` → 통과
③ `edge_score=None` + `fail_open=True` → 통과(fail-open 기록), `False` → 차단
④ `min_rr=2.0` 에서 stop 1.5%/tp 2.5%(rr=1.67) → 차단 / stop 1.5%/tp 4.5%(rr=3) → 통과
⑤ `stop_mode="atr"`: `atr_pct=0.02`·mult 1.5/3.0 → stop 0.03·tp 0.06, `max_stop_pct` 클램프 동작
⑥ atr_pct 결측 → fixed(1.5/2.5) 폴백, `algo_json.stop["model"]=="fixed"`
+ 기존 `trader_core/tests`·`runner/tests` 전부 그린 유지(기존 기대값 수정 금지).

## 검증 (실행 출력 원문 첨부)

```
cd /mnt/c/Users/jhshi/analyist_dd/trader-agent
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_edge_gate.py -q
/mnt/c/Users/jhshi/Python312-64/python.exe -c "
from trader_core.config import Config; c=Config(); print(c.min_edge_score,c.min_rr,c.edge_gate_fail_open, all(k in c.as_dict() for k in ('min_edge_score','min_rr','edge_gate_fail_open')))
from trader_core.profiles import profile_for as p; d=p('daytrading'); s=p('swing'); print(d.stop_mode,d.atr_stop_mult,d.atr_tp_mult,d.stop_pct,d.tp_pct); print(s.stop_mode)"
```

## 금지

- `git` 금지(커밋·사본 동기화는 오케스트레이터). `max_invested_pct`·`daily_max_trades`·
  `max_open_positions`·`post_exit_cooldown_*` 변경 금지(사용자 승인 항목).
- swing/close/rank_momentum 정책 변경 금지. 주문 함수 시그니처·저널 스키마 변경 금지.
- 피드측 코드(`analyist_dd/scripts/**`) 수정 금지 — 짝 스펙의 영역.
- 위 5파일 외 수정 금지, 기존 테스트 기대값 수정 금지, 리포 밖 파일 생성 금지.
