# 설정 계층·사본 동기화·모노레포 (analyist_dd ↔ trader-agent)

코드를 고치거나 배포할 때 자주 어긋나는 네 지점: 설정 화이트리스트, 프리셋, 사본 두 개, 중첩 레포.

## 1. 설정은 `as_dict()` 화이트리스트를 통과해야 먹는다

- `Config.as_dict()` 가 env(`TRADER_CORE_*`)·`config.json` 오버라이드의 **허용 목록**이다. 여기 빠진 필드는 어떤 값을
  어떤 값을 줘도 **조용히 기본값**이 쓰인다(예외·경고 없음) → "설정을 변경했는데 반영이 안 된다"의 1순위 원인.
- 필드를 건드리면 ① `as_dict()` 에 넣고 ② `{f.name for f in dataclasses.fields(Config)}` == `set(as_dict())` 을
  테스트로 고정한다(`trader_core/tests/test_config_portability.py`). 확인은 추측이 아니라
  `python3 -c "from trader_core.config import Config; print(Config().as_dict())"`.

## 2. 프리셋은 "신규 사용자 안전선"이고, 실사용 한도와 다릅니다

- `presets/*.json` + `--preset <name>`( `run_market_loop.py` · `run_trader_core.py` ).
  `paper_safe` = `dry_run:true` · 총노출 30% · 종목 10% · `kelly_cap 0.25`; `rank_only` = `feed_source: rank`.
- 적용 집합: 기본값 → 프리셋 → `config.json` → env. 그 순서는 `trader_core/tests/test_presets.py` 가 고정한다.
- **프리셋 수치를 현행 계좌 한도로 인용하지 마라.** 실사용 값은 `Config()` 실측(`daily_max_trades`·`max_open_positions`·
  `max_position_pct`·`max_invested_pct`·`sizing_mode`)이고 프리셋보다 훨씬 크다 — 리포트에 프리셋(10%/30%)을 쓰면
  탑재된 실한도(1/3·100%)를 놓고 "한도초과"로 오보한다.
- 새 머신·새 사용자에서는 첫 실행 전 `tools/preflight.py` 로 경로·파이썬 비트수·브리지 포트를 확인한다
  (32/64비트를 바꿔치기하면 COM 단계에서만 실패해 원인이 늦게 드러난다).

## 3. 사본이 두 개다 (WSL 레포 ↔ Windows 라이브)

- 실행하는 것은 **라이브 사본**이고, 지금 보고 있는 파일이 어느 쪽인지 먼저 확인한다. 코드를 고쳤으면 보고에
  "어느 사본을 고쳤고・어느 사본을 라이브가 쓴다"를 명시한다.
- 동기화 도구: analyist_dd 의 `scripts/sync_trader_agent.py` — 기본은 diff, `--push`(레포→라이브) / `--pull` / `--apply`.
  추적 파일만 비교하고 **삭제는 하지 않으며** vendor(`creon_extracted_v1/` 등)는 제외한다.
- **함정: `git ls-files` 는 비ASCII 경로를 따옴표로 감싸 출력**한다 → 그대로 비교하면 vendor 파일이
  '라이브에만 있음'으로 오분류된다. 반드시 `ls-files -z`.
- 라이브 사본의 `.git` 은 지우지 않는다(롤백 근거). 레포를 정리할 때는 라이브에서 `git remote remove origin` 만 한다.

## 4. 중첩 레포를 모노레포로 합치기

- `git subtree add` 는 워킹트리가 더러우면 거부되고, `.git` 이 있는 디렉터리를 그냥 복사하면 중첩 repo 가 된다
  → `git -C <nested> archive HEAD | tar -x -C <dest>` 로 **추적 파일만** 풀어 넣는다.
- 이전 레포의 `.gitignore`(`data/`, `reports/`, `journal/*.sqlite3`)를 상위 `.gitignore` 에 합친다.
- **중첩 디렉터리를 통째로 무시하는 규칙이 남아 있으면 `git add <path>` 가 조용히 아무것도 안 한다** →
  `git check-ignore -v <path>` 로 어느 규칙이 걸렸는지 확인하고 그 줄을 지운다.
- Windows 경로는 하드코딩하지 않는다: `.bat` 은 `%~dp0`, PowerShell 은 `$PSScriptRoot` 로 레포 루트를 잡고
  `TRADER_REPO`/`TRADER_PY32`/`TRADER_PY64` 로 덮어쓴다. 예약작업 XML(`tools/tasks/*.xml`)은 절대경로가
  박히므로 커밋하지 않고 `register_tasks.ps1` 로 생성한다.
