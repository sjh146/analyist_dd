구현과 검증이 완료되었습니다. 최종 보고입니다.

---

# 보고 — audit_safety 한도 검사 복구 (스펙 `docs/spec_audit_limits_source.md`)

## 변경 파일 (스펙에 열거된 2파일만)

**1. `scripts/audit_safety.py`** (+131/−23, 추가만·기존 키 유지)
- 상단 docstring: WHY(2026-11-17) 추가 — 낡은 10%/30% 하드코딩, 정규식이 `max_position_pct`/`max_invested_pct`를 못 찾아 **종목당·총노출 두 축이 한 번도 비교된 적 없다**(통과가 아니라 미실행)는 결함 요약 + `dc8256b` 자매 작업 참조 + 실측 조회 명령 2개를 주석으로 기록.
- 33행 하드코딩 `LIMITS` 제거 → `from risk_limits import execution_limits` + `money_scoreboard.py`와 같은 `sys.path` 보정 관용구. 모듈 전역 `LIMITS, LIMITS_META = execution_limits()`.
- `config_limits()`: 정규식 긁기 → 실행 설정(Config) 읽기로 교체. 시그니처 유지(dict 반환, 빈 dict 허용 — `loaded=False`면 `{}`). 출처는 `limits_sources()`/`LIMITS_META`로 노출.
- 신규: `policy_limits_receive()`(소스 수신 — `config/objective.json` envelope 읽기) → `policy_limits_parse()`(파서 생성 — `max_entries_per_day→max_daily_trades`, `max_position_pct→per_stock_pct`, `max_invested_pct→total_exposure_pct` 매핑) → `main()` 저장(3분리).
- `main()` 2) 한도 대조: 정책 사본과 불일치(>1e-9) 시 스펙 규격 그대로 `{"check": "limit_mismatch", "detail": "…: 실행 X vs 정책 Y (출처 실행=config:…, 정책=…/objective.json envelope)"}` 경보. `rec`에 `limits`(실행값 4개)·`limits_meta`(출처)·`limits_note`(max_open_positions는 정책에 키 없어 대조 안 함 사유) **추가**(기존 `rec["info"]["limits"]` 등 삭제·이름변경 없음).
- 자기신고: `_limits_claim`이 `dq_claim.record_claim` 재사용(`table_name="policy_envelope"`, source=매핑 축 수 / claimed=비교 축 수 / persisted=불일치 경보 수 — claimed<source면 기존 Prometheus `RunnerClaimGap`이 울려 "검사가 안 돌았다"가 다시는 조용히 넘어가지 않음). 멱등: `safety_<date>.json` 덮어쓰기(경보 매 실행 재구성), claim은 기존 append 규약.

**2. `tests/test_audit_safety_exit_window.py`** (+1행) — **변경 사유**: `main()`이 `_limits_claim`(DB 자기신고)을 새로 호출하게 되어 기존 테스트가 실 DB 접속 시도를 하게 되므로(금지사항), `_patch_env`의 외부 접점 monkeypatch 목록에 `monkeypatch.setattr(mod, "_limits_claim", lambda **kw: "mocked-ok")` 1행을 추가했습니다. 기대값·assert는 전혀 수정하지 않았습니다(스펙 허용 범위: 코드 보강, 기대값 수정 금지 준수).

## 검증 — 실제 실행 출력 원문

```
$ /usr/bin/python3 -c "import sys;sys.path.insert(0,'scripts');import audit_safety as a;print(a.config_limits())"
{'max_daily_trades': 3, 'per_stock_pct': 0.3333333333333333, 'max_open_positions': 3, 'total_exposure_pct': 1.0}
```
→ 정규식 실패로 키가 빠지지 않고 **4값 모두** 나옴 (기대값 일치).

```
$ /usr/bin/python3 -c "import sys;sys.path.insert(0,'scripts');import audit_safety as a;print(a.limits_sources() if hasattr(a,'limits_sources') else a.LIMITS_META)"
{'source': 'config:/mnt/c/Users/jhshi/analyist_dd/trader-agent', 'loaded': True, 'values': {'max_daily_trades': 3, 'per_stock_pct': 0.3333333333333333, 'max_open_positions': 3, 'total_exposure_pct': 1.0}, 'fallback': {'max_daily_trades': 3, 'per_stock_pct': 0.1, 'max_open_positions': 3, 'total_exposure_pct': 0.3}, 'candidates_tried': ['/mnt/c/Users/jhshi/analyist_dd/trader-agent', '/home/jhshi/analyist_dd/trader-agent']}
```
→ 출처가 기대대로 `config:/mnt/c/.../trader-agent`.

```
$ /usr/bin/python3 -m pytest tests/test_audit_safety_exit_window.py -q
............                                                             [100%]
12 passed in 0.58s
```
→ 전부 통과 (실행시간 0.58s — DB 접속 시도 없음을 확인).

추가 확인(브리지·DB 미접속, 읽기만):
```
receive err: None
policy mapped: {'max_daily_trades': 3, 'per_stock_pct': 0.3333333333333333, 'total_exposure_pct': 1.0}
compared=3 mismatch=0
sample alert detail: total_exposure_pct: 실행 1.0 vs 정책 0.3 (출처 실행=config:/mnt/c/Users/jhshi/analyist_dd/trader-agent, 정책=/home/jhshi/analyist_dd/config/objective.json envelope)
```
→ 정책 사본과 **불일치 0건**(스펙 기대), `max_open_positions`는 대조 대상에서 제외되고 `rec["limits_note"]`로만 기록됨. 경보 문구 규격도 스펙 예시와 일치.

## 금지사항 준수

- 실 브리지·실 DB·네트워크: 접속 없음 (`main()` 미실행, 검증은 읽기 전용 함수만).
- `config/objective.json`·`trader_core/config.py`·`risk_limits.py`·`money_scoreboard.py`·정책 파일: **읽기만**, 수정 없음.
- 리포 밖 파일 생성 없음 — 산출물은 gitignore된 표준 캐시(`__pycache__/`, `.pytest_cache/`)뿐이며 그 밖의 신규 파일 없음.
- 커밋/브랜치 등 상태 변경 git 명령: 실행 안 함. 다만 보고용으로 **읽기 전용** `git diff --stat`, `git status --porcelain`을 두 차례 실행했습니다 — 커밋·스테이징·체크아웃 등 상태 변경은 전혀 없음을 공개합니다.
