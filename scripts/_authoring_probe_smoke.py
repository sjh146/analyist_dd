#!/usr/bin/env python3
"""AUTHPROBE1 — 위임 저작 경로 1회 실제 검증(안전 대상) 스모크.

WHY (2026-10-04): 위임 저작 경로(ask_claude.sh build → researcher_cycle.ensure_artifact)가
"실제로 파일을 만들고 3단 검증을 통과한" 기록이 원장에 **0건**이다. 실측 조회:
    grep -c "저작" data/reports/researcher_ledger.jsonl        → 5건
    grep -c "delegate_rc" data/reports/researcher_ledger.jsonl → 0
5건 전부 "산출물 이미 존재 — 생략"(R10~R12)이었고, 2026-09-28 실측으로 R11 은 생략인데
[저작실패] 라벨이 붙는 라벨 버그까지 있었다(researcher_cycle.py:218 주석 참조). 즉 이 경로는
'새 파일 저작 → ① 존재 ② 구문 ③ 항목 check 통과'를 한 번도 끝까지 실증한 적이 없다.
2026-10-04 금지경로 가드가 배선된 뒤의 **첫 저작이 이 파일 자체**다 — 이 파일이 남고 구문검사를
통과하고 자기신고가 기록되는 것 자체가 가설("위임 저작 경로가 실제로 파일을 만들고 3단 검증을
통과한다")의 증거다.

실측 근거 (조회 명령과 결과, 2026-10-04):
    ls -la scripts/ask_claude.sh scripts/protected_paths.py
        → 10,917B / 3,198B — build 모드·가드 배선 실존
    python3 scripts/protected_paths.py build scripts/_authoring_probe_smoke.py "<스펙>"
        → rc=0 — 이 파일은 금지 경로 아님(안전 대상)
    grep -n "os.path.exists(tpath)\\|py_compile\\|eval_check(item)" scripts/researcher_cycle.py
        → 264·288(① 존재) / 293(② 구문) / 381(③ 항목 check) — 3단 게이트 코드 실존
    ls -la docs/spec_R10.md docs/spec_R11.md docs/spec_R12.md
        → 2,129B / 3,300B / 2,611B (2026-09-25 20:11~20:45) — build 위임 산출 보고서 선례
    docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT COUNT(*) FROM dq_runner_claim"
        → 99행, 이 중 runner LIKE 'build_%' OR runner LIKE '%author%' → 17행 — 저작 산출물 자기신고 선례

검사(매 실행 재측정, 파일 쓰기 없음):
  1. 금지경로 가드 — protected_paths.violation() 이 이 파일을 차단하지 않는가
  2. 저작 진입부 — ask_claude.sh build 모드(ALLOW_WRITE)와 가드 배선이 실존하는가
  3. 3단 검증 게이트 — researcher_cycle.py 에 ① os.path.exists ② py_compile ③ eval_check 가 있는가
  4. 선행 저작 증거 — docs/spec_R10~12.md 3건이 남아 있는가
  5. 자기신고(3분리) — dq_claim.record_claim 재사용:
     source=1(스펙 수신) / claimed=1(파서 생성=저작 파일) / persisted=디스크 실존(실측)

멱등성: 읽기 전용 점검 + dq_runner_claim 에 실행 1회당 1행 append(러너 공통 규약). 파일을
만들거나 지우지 않으므로 재실행해도 상태를 오염하지 않는다. DB 미연결 시 자기신고만 WARN 으로
남기고 판정을 깨지 않는다(dq_claim 설계 규약).

사용: /usr/bin/python3 scripts/_authoring_probe_smoke.py    (rc=0 → 통과)
[검증 명령] python3 -m py_compile scripts/_authoring_probe_smoke.py  [성공 기준] 구문 통과
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

FAILS: list[str] = []


def check(name: str, cond: bool, note: str = "") -> None:
    print("  {0} {1} {2}".format("PASS" if cond else "FAIL", name, note))
    if not cond:
        FAILS.append(name)


def _record_claim() -> None:
    """저작 경로 1회 실행의 자기신고(3분리)를 dq_runner_claim 에 남긴다.

    source_rows=1   : AUTHPROBE1 스펙 1건 수신(소스 수신)
    claimed_rows=1  : 위임 저작이 만들어낸 파일 1개(파서 생성)
    persisted_rows  : scripts/_authoring_probe_smoke.py 의 디스크 실존 여부(실제 저장) — 매 실행 실측
    """
    # .env 는 컨테이너 좌표(POSTGRES_HOST=postgres)다. dq_claim._open_conn 이 호스트 실행을
    # 127.0.0.1:POSTGRES_HOST_PORT 로 되돌리므로 커넥션 생성은 _open_conn 에 맡긴다
    # (audit_safety.py:159 · yf_claim_from_phase_log.py:34 와 같은 규약).
    envp = os.path.join(REPO, ".env")
    if not os.environ.get("POSTGRES_PASSWORD"):
        try:
            for line in open(envp, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass
    try:
        from dq_claim import _open_conn, record_claim  # noqa: PLC0415 — psycopg2 지연 import 규약
        persisted = 1 if os.path.exists(os.path.join(SCRIPTS, "_authoring_probe_smoke.py")) else 0
        conn = _open_conn()
        try:
            record_claim(conn, runner="authprobe_authoring_path",
                         table_name="authoring_probe_target",
                         claimed_rows=1, persisted_rows=persisted, source_rows=1,
                         note="spec=AUTHPROBE1 target=scripts/_authoring_probe_smoke.py "
                              "source(스펙수신)=1 claimed(파서생성=파일)=1 "
                              "persisted(디스크실존)=%d" % persisted)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM dq_runner_claim WHERE runner = %s",
                        ("authprobe_authoring_path",))
            n = int(cur.fetchone()[0])
            cur.close()
        finally:
            conn.close()
        print("  PASS 자기신고(3분리) 기록·확인 dq_runner_claim 누적 %d행 (runner=authprobe_authoring_path)" % n)
    except Exception as exc:  # noqa: BLE001 — 자기신고 실패가 판정을 깨면 안 된다(dq_claim 규약)
        print("  WARN 자기신고(3분리) 기록 실패({0}: {1}) — 판정에는 영향 없음".format(
            type(exc).__name__, exc))


def main() -> int:
    print("[AUTHPROBE1] 위임 저작 경로 1회 실제 검증(안전 대상)")
    sys.path.insert(0, SCRIPTS)

    # 1. 금지경로 가드 — 이 파일(안전 대상)이 저작 위임을 통과한 경로인지 재확인.
    try:
        import protected_paths
        blocked = protected_paths.violation("build", "scripts/_authoring_probe_smoke.py",
                                            "AUTHPROBE1 저작 경로 1회 실제 검증(안전 대상)")
        check("금지경로 가드 통과(안전 대상)", blocked is None,
              "" if blocked is None else "차단: %s" % blocked)
    except Exception as exc:  # noqa: BLE001
        check("금지경로 가드 통과(안전 대상)", False, "가드 로드 실패(fail-closed): %s" % exc)

    # 2. 저작 진입부 — build 모드(쓰기 허용)와 가드 배선 실존.
    entry = os.path.join(SCRIPTS, "ask_claude.sh")
    has_build = has_guard = False
    if os.path.exists(entry):
        txt = open(entry, encoding="utf-8").read()
        has_build = "build)" in txt and "ALLOW_WRITE" in txt
        has_guard = "protected_paths.py" in txt
    check("저작 진입부(ask_claude.sh build 모드)", has_build, "scripts/ask_claude.sh")
    check("진입부 금지경로 가드 배선", has_guard,
          "scripts/protected_paths.py 단일 진실원 호출")

    # 3. 3단 검증 게이트 — ① 파일 존재 ② 구문(py_compile) ③ 항목 check 코드가 실존하는가.
    #    실측: grep -n "os.path.exists(tpath)\|py_compile\|eval_check(item)" scripts/researcher_cycle.py
    #          → 264·288 / 293 / 381
    gate = None
    rcp = os.path.join(SCRIPTS, "researcher_cycle.py")
    if os.path.exists(rcp):
        src = open(rcp, encoding="utf-8").read()
        gate = ("os.path.exists(tpath)" in src, '"py_compile"' in src, "eval_check(item)" in src)
    check("3단 검증 게이트 코드(존재·구문·항목)", bool(gate) and all(gate),
          "exists/compile/check=%s" % (gate,))

    # 4. 선행 저작 증거 — build 위임이 남긴 산출 보고서 3건(2026-09-25 실측).
    specs = ["docs/spec_R10.md", "docs/spec_R11.md", "docs/spec_R12.md"]
    found = [s for s in specs if os.path.exists(os.path.join(REPO, s))]
    check("선행 저작 증거(docs/spec_R10~12.md)", len(found) == 3, "%d/3건 존재" % len(found))

    # 5. 자기신고(3분리) — 소스 수신 / 파서 생성 / 실제 저장을 dq_runner_claim 에 남긴다.
    _record_claim()

    print("\n{0}".format("ALL PASS" if not FAILS else "FAIL %d" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
