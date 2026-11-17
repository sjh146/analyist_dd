모든 변경과 검증이 완료되었습니다. 최종 보고입니다.

---

# 구현 보고 — 계기판 한도 축 단일 진실원 (spec_scoreboard_limits_source)

## 변경 파일 (스펙 4파일 범위 내)

### 1. 신규 `scripts/risk_limits.py`
- 파일 상단 docstring에 WHY: `data/state/ceo_priority.json` 2026-11-16 에스컬레이션 원문 인용(실제 파일 조회로 확인) + 실측 명령과 결과값 기록.
- `execution_limits(candidates=None)`: 후보 순서 `TRADER_AGENT_DIR`(미설정 확인) → Windows 라이브 사본 → 리포 내 사본. 각 후보에서 `trader_core.config.Config()`를 읽고 매핑(`max_daily_trades←daily_max_trades`, `per_stock_pct←max_position_pct`, `max_open_positions←max_open_positions`, `total_exposure_pct←max_invested_pct`).
- import 후 `sys.path`·`sys.modules` 원상복구(검증으로 오염 없음 확인). 어떤 후보도 실패 시 `FALLBACK`+`loaded=False`, 예외 없음(검증으로 확인).
- `LIMITS, LIMITS_META = execution_limits()` 모듈 노출 + `claim()` 3분리 자기신고 헬퍼.
- 실측 근거: 라이브 사본과 리포 내 사본 모두 `3 0.3333333333333333 3 1.0` 동일함을 확인.

### 2. `scripts/money_scoreboard.py` (최소 수정 5곳)
- docstring 26행 하드코딩 문구 → `한도: 실행 설정(trader_core/config.py)의 일 거래수·종목당·동시보유·총노출 — 값은 출력의 capital_utilization.limits 참조` (숫자 제거).
- 하드코딩 `LIMITS` dict 제거 → `from risk_limits import LIMITS, LIMITS_META, claim as limits_claim`.
- `_paths`·`risk_limits` import가 **실행·importlib 양쪽**에서 되도록 `sys.path` 보정 관용구 추가(기존에는 `spec_from_file_location` 로드 시 `No module named '_paths'`로 수집 자체가 실패하는 상태였음 — 테스트 통과를 위해 필요).
- `capital_utilization` 생성부 바로 옆에 최상위 `rep["limits_meta"] = LIMITS_META` 추가(기존 키 삭제·이름변경 없음, 추가 키만).
- 기존 `claim`에 `"limits": limits_claim()` 추가(소스 수신/파서 생성/실제 저장 3분리). `dq_claim.record_claim`은 `dq_runner_claim` INSERT라 읽기 전용 계약에 위배되어 미사용 — 그 사유를 claim note에 명시(기존 파일의 동일 관례).

### 3. `tests/test_money_scoreboard.py` (기대값 1곳만)
- 183행 부근 `assert cu["limits"] == {...0.10/0.30...}` → `assert cu["limits"] == LIMITS` + `LIMITS_META["loaded"]`면 실행 설정값(`pytest.approx(1/3)` 등)과 일치 검사. 기존 관용구(tests → scripts 경로 삽입) 사용. 그 외 테스트 무수정.

### 4. `docs/QUANT_SCOREBOARD.md` — **미수정**
- 조회: `grep -n "종목당|동시|총 30|30%|10%|3건|한도"` → 매치 0. 스펙대로 건드리지 않음.

### 기존 파일 불가피 수정 1곳(스펙 요구 충족용)
- `claim.persisted_rows`에서 `out_dir` 키 제거: 스펙 검증이 **서로 다른** `--out-dir` 2회 실행의 **바이트 동일**을 요구하는데, `out_dir`이 산출물에 내장되어 있으면 어떤 실행에서도 결코 동일해질 수 없음(변경 전 실측 diff로 확인). 스펙 멱등 계약을 위해 제거했고, 그 사유를 claim note에 남김. 경로는 `main()`의 stdout 요약 줄에 그대로 출력됨.

## 검증 실행 결과 (출력 원문)

**① risk_limits 헬퍼**
```
$ /usr/bin/python3 -c "import sys; sys.path.insert(0,'scripts'); import risk_limits as r; print(r.LIMITS, r.LIMITS_META['source'], r.LIMITS_META['loaded'])"
{'max_daily_trades': 3, 'per_stock_pct': 0.3333333333333333, 'max_open_positions': 3, 'total_exposure_pct': 1.0} config:/mnt/c/Users/jhshi/analyist_dd/trader-agent True
```

**② pytest**
```
$ /usr/bin/python3 -m pytest tests/test_money_scoreboard.py -q
.............                                                            [100%]
13 passed in 0.40s
```

**③④ 픽스처 모드 2회 실행 (멱등)**
```
$ /usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fixture
[money-scoreboard] /tmp/msb_fixture/scoreboard_2026-11-17.{json,md} · mode=fixture · 실현손익 -1,446원 (3건) · 보유 0 · 1순위: 익일 시가 청산창(09:00-09:10) 이탈 — 체결가가 당일 시가를 하회
$ /usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fixture2
[money-scoreboard] /tmp/msb_fixture2/scoreboard_2026-11-17.{json,md} · mode=fixture · 실현손익 -1,446원 (3건) · 보유 0 · 1순위: 익일 시가 청산창(09:00-09:10) 이탈 — 체결가가 당일 시가를 하회
$ diff -r /tmp/msb_fixture /tmp/msb_fixture2 && echo "BYTE-IDENTICAL-OK"
BYTE-IDENTICAL-OK
```

**추가 검증(산출물 내용 + fallback 계약)**
```
limits_meta: {"source": "config:/mnt/c/Users/jhshi/analyist_dd/trader-agent", "loaded": true, "values": {"max_daily_trades": 3, "per_stock_pct": 0.3333333333333333, "max_open_positions": 3, "total_exposure_pct": 1.0}, ...}
capital_use.limits: {'max_daily_trades': 3, 'per_stock_pct': 0.3333333333333333, 'max_open_positions': 3, 'total_exposure_pct': 1.0}
fallback-ok: {'max_daily_trades': 3, 'per_stock_pct': 0.1, 'max_open_positions': 3, 'total_exposure_pct': 0.3} fallback False
no-pollution-ok: sys.modules/sys.path 원상복구 확인
```

## 금지 준수 확인
- 정책 값 변경 없음(`trader_core/config.py`·프리셋·`.env`·`audit_safety.py` 무수정) — 읽기만.
- 실 브리지·실 DB 미접속(`--no-live` 픽스처 모드만), 네트워크 호출 없음. `risk_limits`의 Config 읽기는 파일 소스 읽기일 뿐 실행 주체의 정책 파일 수정이 아님.
- 리포 밖 파일 생성 없음(임시 산출물은 /tmp에만). git 명령 미실행(커밋은 오케스트레이터 담당).
