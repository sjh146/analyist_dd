# 스펙 — 감사(audit_safety) 한도 검사 복구: 실행 설정 ↔ 정책 사본 대조

작성 2026-11-17. **사용자 승인**: "응 계속 진행해줘" (한도 축 정합 작업의 연속).
선행 커밋 `dc8256b`(계기판이 `trader_core/config.py` Config 를 직접 읽게 함)의 자매 작업이다.

## 발견된 결함 (2026-11-17 실측 — 재조사 금지)

`scripts/audit_safety.py` 의 한도 검사가 **사실상 죽어 있다**:

1. `LIMITS = {"max_daily_trades": 3, "per_stock_pct": 0.10, "max_open_positions": 3, "total_exposure_pct": 0.30}` (33행) — 계기판과 같은 낡은 10%/30% 하드코딩.
2. `config_limits()` (56행) 는 `trader_core/config.py` 를 **정규식으로** 훑어 `per_stock_pct`/`total_exposure_pct` 키를 찾는다.
   그런데 config.py 의 실제 필드명은 `max_position_pct` / `max_invested_pct` 이다 →
   `cfg` 에 그 두 키가 **아예 없고**, 212행의 `if k in cfg` 가 조용히 건너뛴다.
   즉 종목당·총노출 두 축은 **한 번도 비교된 적이 없다**(검사가 통과한 게 아니라 안 돌았다).

실측 확정값:
- 실행 설정 `Config()`: `daily_max_trades=3`, `max_position_pct=0.3333333333333333`, `max_open_positions=3`, `max_invested_pct=1.0`
- 정책 사본 `config/objective.json` → `envelope`: `max_entries_per_day=3`, `max_position_pct=0.3333333333333333`, `max_invested_pct=1.0` (`max_open_positions` 키는 **없다** — envelope.source 에 "소스 오브 트루스는 트레이더 설정"이라 적혀 있다)

## 목표

감사가 **실행 설정을 정본**으로 삼아 (a) 값을 기록하고 (b) 정책 사본(objective.json envelope)과
어긋나면 **어느 값이 어디서 왔는지 함께** 경고한다. 새 정책 값을 정하지 않는다.

## 변경 대상 (이 2파일만)

1. `scripts/audit_safety.py`
   - 33행 하드코딩 `LIMITS` 제거 → `from risk_limits import execution_limits` (같은 `scripts/` 디렉터리. `sys.path` 보정 관용구 필요 — `money_scoreboard.py` 가 쓴 방식 참고).
   - `config_limits()` 를 **정규식 긁기에서 실행 설정 읽기로 교체**:
     ```python
     def config_limits():
         """실행 설정(Config)에서 한도 4값 + 출처 메타. 실패하면 {} (기존 계약: 빈 dict 허용)."""
     ```
     `execution_limits()` 의 `(limits, meta)` 를 쓰고 `limits_meta` 를 함께 돌려줄 수 있게 반환 형태를 dict 로 유지하되
     출처는 별도 함수 `limits_sources()` 또는 모듈 전역 `LIMITS_META` 로 노출한다. **기존 호출부 시그니처를 깨지 말 것**.
   - 정책 사본 대조: `config/objective.json` 의 `envelope` 를 읽어
     `max_entries_per_day→max_daily_trades`, `max_position_pct→per_stock_pct`, `max_invested_pct→total_exposure_pct`
     로 매핑해 실행값과 비교한다. 불일치(>1e-9)면 기존 `rec["alerts"]` 관용구로
     ```python
     {"check": "limit_mismatch", "detail": "total_exposure_pct: 실행 1.0 vs 정책 0.3 (출처 실행=config:…, 정책=config/objective.json envelope)"}
     ```
     를 남긴다. `max_open_positions` 는 정책 사본에 없으므로 **대조하지 않고** 실행값만 `rec` 에 남긴다(그 사유를 detail 이 아니라 `rec["limits_note"]` 한 줄로).
   - `rec` 에 `"limits"`(실행값 4개)와 `"limits_meta"`(출처)를 남긴다. 기존 키 삭제·이름변경 금지(추가만).
   - 파일 상단 docstring 에 WHY(위 '발견된 결함' 요약 + 커밋 `dc8256b` 참조)를 남긴다.
2. `tests/test_audit_safety_exit_window.py` — **기존 테스트가 깨지면 코드를 고친다**(테스트 기대값 수정 금지).
   단, `config_limits()` 가 정규식이 아니라 Config 를 읽도록 바뀌었으므로 그 함수를 직접 검사하는 테스트가
   있다면 "실행 설정과 일치"를 검사하도록 **의미가 강화된 형태로만** 바꿔도 된다(그 경우 변경 사유를 보고에 적는다).

## 검증 (실제 실행, 출력 원문을 보고에 붙인다)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -c "import sys;sys.path.insert(0,'scripts');import audit_safety as a;print(a.config_limits())"
/usr/bin/python3 -c "import sys;sys.path.insert(0,'scripts');import audit_safety as a;print(a.limits_sources() if hasattr(a,'limits_sources') else a.LIMITS_META)"
/usr/bin/python3 -m pytest tests/test_audit_safety_exit_window.py -q
```
기대:
- `config_limits()` 가 `{'max_daily_trades': 3, 'per_stock_pct': 0.3333…, 'max_open_positions': 3, 'total_exposure_pct': 1.0}` (정규식 실패로 키가 빠지지 않는다).
- 출처가 `config:/mnt/c/.../trader-agent` 로 나온다.
- 불일치가 없으므로 `limit_mismatch` 경고는 **0건**, 정책에 없는 `max_open_positions` 는 `limits_note` 에만 언급.
- pytest 전부 통과.

## 금지

- `git` 명령 실행 금지(커밋은 오케스트레이터).
- `config/objective.json`·`trader_core/config.py`·프리셋·`.env` 수정 금지(**읽기만**).
- `scripts/money_scoreboard.py`·`scripts/risk_limits.py` 수정 금지(이미 확정 — 읽기만).
- 실 브리지·실 DB 접속 금지, 네트워크 호출 금지.
- 위 2파일 외 수정 금지(임시 산출물은 /tmp). 기존 테스트 기대값 수정 금지(위 단서 조항 제외).
