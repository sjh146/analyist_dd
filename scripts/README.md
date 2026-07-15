# Test-Fix Loop System

Automated test-fix loop for the money_dd project. Runs tests, parses failures,
invokes the fix agent, and loops until all tests pass or the iteration limit is
reached.

---

## Quick Start

```bash
# Run the full test-fix loop (max 5 iterations)
bash scripts/test_fix_loop.sh

# Dry-run — print what would happen, make no changes
bash scripts/test_fix_loop.sh --dry-run

# Run tests only, report failures, skip fix attempts
bash scripts/test_fix_loop.sh --skip-fix

# Custom max iterations
MAX_ITER=3 bash scripts/test_fix_loop.sh
```

---

## Script Reference

| Script | Purpose |
|--------|---------|
| `test_fix_loop.sh` | Main orchestrator — runs tests, parses failures, fixes, loops |
| `run_all_tests.sh` | Unified test runner — checks Docker health, runs pytest, saves JSON results |
| `run_tests_in_docker.sh` | Quick pytest run inside the xgboost-ml Docker container |
| `parse_test_results.py` | Parses pytest JSON or JUnit XML reports into failure detail JSON |
| `fix_agent.sh` | Reads latest failures, logs them, tracks iteration count (5 max) |
| `git_safety.sh` | Git safety wrapper — pre-fix stash, rollback, status check |
| `emergency_revert.sh` | Emergency revert of last N commits |
| `generate_tests.sh` | Auto-generates smoke tests for services missing test directories |
| `notify.sh` | Slack webhook notification (or stdout fallback) |
| `loop_monitor.sh` | Real-time dashboard showing loop progress |
| `inject_failure.sh` | Intentionally injects/restores test failure for validation |

### test_fix_loop.sh

Main orchestrator. Runs the full loop: test -> parse -> fix -> repeat.

**Usage:**
```bash
bash scripts/test_fix_loop.sh [--dry-run] [--skip-fix]
```

**Options:**
- `--dry-run` — Print what would be done without executing any commands
- `--skip-fix` — Run tests and report failures, but do not attempt fixes

**Exit codes:**
- `0` — All tests passed
- `1` — Failures remain after max iterations, or an error occurred

### run_all_tests.sh

Unified test runner. Checks Docker service health, runs pytest inside the
xgboost-ml container, optionally runs a host-based E2E test, and writes a JSON
result snapshot.

**Steps:**
1. Check all Docker services are `Up` via `docker compose ps`
2. Run `pytest tests/` inside the xgboost-ml container (tries `--json-report`
   first, falls back to `--junitxml`)
3. Run `tests/test_e2e_pipeline.py` on the host if it exists
4. Write result to `.omo/evidence/test-run-<timestamp>.json`

**Output:** Writes a JSON file with `docker_services_up`, `pytest`, `e2e`, and
`overall` fields. Exits 0 on pass, 1 on failure.

### run_tests_in_docker.sh

Minimal wrapper for running pytest inside the xgboost-ml container.

```bash
bash scripts/run_tests_in_docker.sh
```

Equivalent to:
```bash
docker compose run --rm xgboost-ml python -m pytest tests/ -v --tb=short
```

### parse_test_results.py

Parses a pytest JSON report (from `pytest-json-report`) or JUnit XML report
into a structured failure JSON consumed by the fix agent.

**Usage:**
```bash
python scripts/parse_test_results.py \
    --input .omo/evidence/pytest-report.xml \
    --output .omo/evidence/latest-failures.json
```

**Input format detection:** By file extension — `.xml` uses JUnit parser,
everything else uses JSON parser.

**Output schema:**
```json
{
  "generated_at": "2026-07-15T21:00:00",
  "input_file": "...",
  "summary": { "total": 10, "passed": 8, "failed": 2, "errors": 0 },
  "failed_tests": [
    {
      "test_name": "test_model.py::test_predict",
      "file": "test_model.py",
      "line": 42,
      "error": "assert False",
      "traceback": "..."
    }
  ],
  "all_passed": false
}
```

### fix_agent.sh

Reads the latest failures from `latest-failures.json`, logs each failure with
its test name, file, line, and error message. Tracks iteration count and
enforces a maximum of 5 fix attempts.

**Usage:** Called by `test_fix_loop.sh` — not intended for direct invocation.

```bash
FIX_ITERATION=1 bash scripts/fix_agent.sh
```

**Exit codes:**
- `0` — No failures to fix (all passed)
- `1` — Max attempts reached or failures file missing/invalid

### git_safety.sh

Safety wrapper for automated fix loops. Provides three modes:

```bash
bash scripts/git_safety.sh pre-fix    # Stash changes, tag HEAD
bash scripts/git_safety.sh rollback   # Reset last auto-fix commit, restore stash
bash scripts/git_safety.sh status     # Show tag/stash/uncommitted counts
```

**pre-fix:** Stashes uncommitted changes (including untracked), records current
HEAD, and creates a tag `auto-fix-attempt-<timestamp>`.

**rollback:** If the last commit message contains `auto-fix` or `fix-attempt`,
resets `HEAD~1`. Then pops the most recent stash. Outputs JSON with
before/after HEAD hashes.

**status:** Reports count of `auto-fix-*` tags, stash entries, and uncommitted
files.

All modes output valid JSON.

### emergency_revert.sh

Emergency revert of the last N commits when the fix loop goes wrong.

```bash
bash scripts/emergency_revert.sh      # Revert last 1 commit
bash scripts/emergency_revert.sh 5    # Revert last 5 commits
bash scripts/emergency_revert.sh 0    # Dry-run — print what would happen
```

Creates a tag `emergency-revert-<timestamp>` at the revert point. Validates
that the requested count does not exceed the total commit history. Outputs
JSON with before/after HEAD hashes and a summary of reverted commits.

### generate_tests.sh

Auto-generates smoke tests for services under `services/*/` that are missing a
`tests/` directory.

**Process:**
1. Skips services that already have a `tests/` directory
2. Scans for main entry points (`app/main.py`, `src/main.py`, `app.py`,
   `main.py`)
3. Extracts class and function names to create import/instantiation/call smoke
   tests
4. Verifies the generated test file has valid Python syntax
5. Removes any test that fails verification

```bash
bash scripts/generate_tests.sh
```

### notify.sh

Sends a notification about loop progress. Supports Slack webhook and stdout
fallback.

```bash
# Stdout fallback (SLACK_WEBHOOK_URL unset or empty)
bash scripts/notify.sh PASS 3 5 19 0 "2m34s"

# Slack webhook
SLACK_WEBHOOK_URL="https://hooks.slack.com/..." \
    bash scripts/notify.sh IN_PROGRESS 2 5 18 1 "1m34s"
```

**Arguments:** `STATUS ITER MAX_ITER [PASSED] [FAILED] [DURATION]`

**STATUS values:** `PASS`, `FAIL`, `IN_PROGRESS`, `MAX_ATTEMPT`

When `SLACK_WEBHOOK_URL` is set, sends a Slack message with formatted blocks.
Otherwise prints to stdout. Always logs to `.omo/evidence/notify.log`.

### loop_monitor.sh

Real-time dashboard showing loop progress from evidence files.

```bash
bash scripts/loop_monitor.sh              # Summary dashboard
bash scripts/loop_monitor.sh --errors     # Include latest error details
bash scripts/loop_monitor.sh --watch      # Auto-refresh every 5 seconds
```

Parses all `test-run-*.json` files in `.omo/evidence/` and displays:
- Total test runs
- Best and latest pass counts
- Current iteration
- Elapsed time
- Per-run bar chart of passed/total
- Latest errors (with `--errors`)

### inject_failure.sh

Intentionally injects a failing test into `services/xgboost-ml/tests/test_model.py`
to validate the fix agent's recovery capability.

```bash
bash scripts/inject_failure.sh inject          # Inject failing test
bash scripts/inject_failure.sh restore         # Restore original file
bash scripts/inject_failure.sh verify          # Test inject + restore cycle
bash scripts/inject_failure.sh verify-pipeline # Full pipeline validation
```

**Modes:**
- `inject` — Appends `test_intentionally_failing` with `assert False` to
  `test_model.py`. Creates a backup first. Idempotent (skips if already
  injected).
- `restore` — Restores from backup, or removes injected lines via `sed` if no
  backup exists.
- `verify` — Runs inject then restore and confirms both work correctly.
- `verify-pipeline` — Full end-to-end test: inject -> run tests -> parse
  results -> detect failure -> restore.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_ITER` | `5` | Maximum loop iterations before manual intervention |
| `FIX_ITERATION` | `0` | Current fix attempt counter (set by orchestrator) |
| `SLACK_WEBHOOK_URL` | (unset) | Slack webhook URL for notifications |

---

## Safety Mechanisms

- **Git stash before every fix attempt** — `git_safety.sh pre-fix` stashes
  uncommitted changes (including untracked files) and tags HEAD before the fix
  agent runs.
- **Auto-fix tags** — Each fix attempt creates a tag
  `auto-fix-attempt-<timestamp>` for traceability.
- **Rollback on failure** — If the fix agent fails, `git_safety.sh rollback`
  resets the last auto-fix commit and restores stashed changes.
- **Max 5 fix attempts** — `fix_agent.sh` exits with error on iteration >= 5.
  The orchestrator also enforces `MAX_ITER` (default 5).
- **Emergency revert** — `emergency_revert.sh` can roll back N commits in one
  command, tagging the revert point.
- **Dry-run mode** — `test_fix_loop.sh --dry-run` prints the planned steps
  without executing anything.
- **Skip-fix mode** — `test_fix_loop.sh --skip-fix` runs tests and reports
  failures without attempting any fixes.

---

## File Layout

```
.omo/evidence/
├── test-run-<timestamp>.json   # Test result snapshots from run_all_tests.sh
├── latest-failures.json        # Current failures for fix agent (from parse_test_results.py)
├── pytest-report.json          # JSON report from pytest (if --json-report supported)
├── pytest-report.xml           # JUnit XML report from pytest (fallback format)
├── fix-agent.log               # Fix agent activity log
├── test-fix-loop.log           # Loop orchestrator log
├── notify.log                  # Notification log
└── test_model_backup.py        # Backup created by inject_failure.sh
```

---

## Architecture

```
                     +-----------------------+
                     |   test_fix_loop.sh    |
                     |    (Orchestrator)     |
                     +----------+------------+
                                |
            +-------------------+-------------------+
            |                   |                   |
            v                   v                   v
  +-------------------+  +-------------+  +------------------+
  |   run_all_tests   |  |  fix_agent  |  |   git_safety     |
  | (Docker + pytest) |  |  (OpenCode) |  | (stash / tag)    |
  +-------------------+  +-------------+  +------------------+
            |
   +--------+--------+
   |                  |
   v                  v
run_tests_in      parse_test_results.py
_docker.sh        (JSON / JUnit parser)
```

**Loop flow:**
1. `test_fix_loop.sh` calls `run_all_tests.sh`
2. `run_all_tests.sh` checks Docker, runs pytest, writes JSON result
3. `test_fix_loop.sh` calls `parse_test_results.py` to extract failures
4. If failures exist, `git_safety.sh pre-fix` stashes and tags
5. `fix_agent.sh` reads failures and logs them
6. `notify.sh` sends progress notification
7. Loop repeats from step 1 until all pass or max iterations reached
8. On max iterations, `git_safety.sh rollback` reverts changes

---

## Troubleshooting

| Symptom | Cause / Solution |
|---------|------------------|
| "Docker not running" | Start Docker services: `docker compose up -d` |
| "git_safety.sh exit 128" | Script needs a git repository. Run `git init` or check you are in the project root. |
| "MAX FIX ATTEMPTS REACHED" | Manual intervention required. Run `bash scripts/emergency_revert.sh 5` to roll back, then investigate the root cause. |
| "test_intentionally_failing found" | A validation injection was not cleaned up. Run `bash scripts/inject_failure.sh restore` to remove it. |
| "No test-run JSON found" | Tests did not produce output. Check Docker is running and pytest can execute. |
| "Missing dependency: ... not executable" | Run `chmod +x scripts/*.sh` to make all scripts executable. |
| Slack notifications not sending | Verify `SLACK_WEBHOOK_URL` is set and `curl` is installed. Without the env var, notifications go to stdout. |
| Loop exits immediately with "ERROR" | Check `run_all_tests.sh` output for Docker or pytest errors. Run it standalone to isolate: `bash scripts/run_all_tests.sh`. |
