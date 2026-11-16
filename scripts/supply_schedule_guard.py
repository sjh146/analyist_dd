#!/usr/bin/env python3
"""수급 수집 스케줄 상시 감시 — 크론이 '사라지면' 수집 정지가 조용히 시작된다.

배경(실측 2026-11-17): root crontab 이 소실돼 `kis_supply.sh`(평일 16:20)가 11-14 이후 실행되지
않았다. 그런데 감시 지표(신선도·R21 지연 프로브·DQ 메트릭)는 **수집이 도는 것을 전제**하므로
"지연 증가"로만 보이고 '스케줄이 없어졌다'는 사실은 아무 데도 안 나왔다. 실측 피해:
  foreign_institutional 날짜별 행수 11-10 814 · 11-11 728 · 11-12 559 · 11-13 **13** · 11-16 **0**
  종목별 최신일 지연 최대 34거래일 / 3거래일 이상 584종목(1,143종목 중)

그래서 스케줄의 **존재 자체**를 수치로 판정한다. 출력의 마지막 숫자 = 결함 수(0 이면 정상).
  cron_defects=<n>

결함 정의
  1) 실행 중인 어떤 crontab 에도 `kis_supply.sh` 줄이 없다
  2) cron 데몬 프로세스가 없다 (스케줄이 있어도 아무것도 실행되지 않는다)

테스트 훅(운영에서는 쓰지 않는다): SUPPLY_CRONTAB_TEXT / SUPPLY_CRON_PS_TEXT 로 원문을 주입하면
`crontab -l`·`ps` 를 부르지 않는다.
"""
import os
import re
import subprocess
import sys

SUPPLY_PATTERN = re.compile(r"kis_supply\.sh")
CRON_DAEMON = re.compile(r"/usr/sbin/cron|(^|/)cron( |$)")


def _run(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
        return p.stdout or ""
    except Exception as e:  # noqa: BLE001 — 감시는 어떤 실패에도 숫자를 내야 한다
        return f"__error__ {e}"


def crontab_text():
    """설치된 crontab 원문(사용자 + 읽을 수 있으면 root). 실패는 빈 문자열."""
    env = os.environ.get("SUPPLY_CRONTAB_TEXT")
    if env is not None:
        return env
    parts = [_run("crontab -l 2>/dev/null")]
    root = _run("crontab -l -u root 2>/dev/null")  # 권한 없으면 빈 문자열
    parts.append(root)
    return "\n".join(parts)


def cron_process_text():
    env = os.environ.get("SUPPLY_CRON_PS_TEXT")
    if env is not None:
        return env
    return _run("ps -eo cmd | grep -v grep")


def defects(ctab, psout):
    out = []
    if not SUPPLY_PATTERN.search(ctab or ""):
        out.append("kis_supply.sh 스케줄 없음 — 수급 수집이 아무 때도 돌지 않는다")
    if not CRON_DAEMON.search((psout or "").replace("/usr/sbin/cron -P", "/usr/sbin/cron")):
        out.append("cron 데몬 미실행 — 스케줄이 있어도 실행되지 않는다")
    return out


def main():
    ctab = crontab_text()
    psout = cron_process_text()
    bad = defects(ctab, psout)
    for b in bad:
        print(f"[defect] {b}")
    line = next((l for l in (ctab or "").splitlines() if SUPPLY_PATTERN.search(l)), "")
    print(f"[info] supply_line={line.strip()[:120] or '(none)'}")
    print(f"cron_defects={len(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
