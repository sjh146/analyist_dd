#!/usr/bin/env python3
"""_pipeline_guard_test.py — 저녁 파이프라인 동시 실행 가드 검증.

배경(실측 2026-10-05 20:0x): 파이프라인(full_pipeline_dd.sh)은 평일 20:00 에 시작해
21:40~23:20 에 끝나고, 그 안에 Phase 2 '챔피언 재학습'(학습 1건)이 있다. 시작 시점의
load1 가드는 파이프라인이 **수집 단계**일 때 통과한다(실측 그 시각 load1=2.2 < 3.5)
→ est 130분짜리 항목이 재학습과 정면으로 겹쳐 서로 3~20배 느려지고, 컨테이너 timeout
여유가 1.5배뿐인 항목은 잘려 결과를 잃는다(하드규칙 5 = 4코어 직렬화).

검증 항목:
  A) pipeline_pid_from_cmdlines — 실행 중 판정(자기매칭·오탐 방어 포함), 순수 함수
  B) guards() — 파이프라인 실행 중엔 est≥60분 항목을 거부하고, 짧은 항목은 통과시키며,
     --force 는 통과시킨다(런처의 명시적 강행을 막지 않는다)
  C) 인스턴트 체크 — 실제 /proc 스캔이 예외 없이 돌고, 반환 pid 는 실재한다
"""
import datetime as dt
import os
import sys

sys.path.insert(0, "scripts")
import model_engineer_cycle as m  # noqa: E402

KST = m.KST
fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got} want={want}")
    if not ok:
        fails.append(name)


NUL = "\0"

# ── A) 순수 함수 — cmdline 판정 ────────────────────────────────────────────────
cases = [
    ("직접 실행", (101, f"bash{NUL}scripts/full_pipeline_dd.sh{NUL}"), (), (101, "full_pipeline_dd.sh")),
    ("크론 래퍼(셸 -c)",
     (102, f"/bin/bash{NUL}-c{NUL}/home/jhshi/cron/evening_pipeline.sh >> /x.log 2>&1{NUL}"), (),
     (102, "evening_pipeline.sh")),
    ("python 인터프리터",
     (106, f"python3{NUL}full_pipeline_dd.sh{NUL}"), (), (106, "full_pipeline_dd.sh")),
    ("자기 pid 는 제외",
     (101, f"bash{NUL}scripts/full_pipeline_dd.sh{NUL}"), (101,), (None, None)),
    # 셸 -c 의 긴 문자열은 argv 원소 1개다(공백 split 으로 쪼개면 오탐) — 스킬의 자기매칭 함정 재판.
    ("셸 -c 긴 문자열(오탐 방어)",
     (103, f"/usr/bin/bash{NUL}-c{NUL}eval 'cd /repo && sed -n 1,10p scripts/full_pipeline_dd.sh | head'{NUL}"),
     (), (None, None)),
    ("읽기만(cat)",
     (104, f"cat{NUL}scripts/full_pipeline_dd.sh{NUL}"), (), (None, None)),
    ("무관한 학습",
     (105, f"python3{NUL}scripts/wf_label_sweep.py{NUL}"), (), (None, None)),
    ("스크립트로 시작하는 셸 문자열(실행으로 인정)",
     (107, f"bash{NUL}scripts/full_pipeline_dd.sh --flag{NUL}"), (), (107, "full_pipeline_dd.sh")),
]
for label, entry, selfp, want in cases:
    chk(f"A pipeline_pid_from_cmdlines {label}", m.pipeline_pid_from_cmdlines([entry], selfp), want)

# ── B) guards() 통합(락·컨테이너·부하 stub) ────────────────────────────────────
saved = (m.running_pid, m.peer_running, m.container_up, m.load1, m.market_hours,
         m.now_kst, m.pipeline_in_flight)
try:
    m.running_pid = lambda *a, **k: None
    m.peer_running = lambda *a, **k: (None, None)
    m.container_up = lambda: True
    m.load1 = lambda: 0.0
    m.market_hours = lambda *a, **k: False
    # 2026-10-05(월) 22:00 — 재생성 창(20:00~20:12) 밖, 다음 재생성은 10-06 20:00.
    m.now_kst = lambda: dt.datetime(2026, 10, 5, 22, 0, tzinfo=KST)
    m.pipeline_in_flight = lambda: (4242, "full_pipeline_dd.sh")

    ok, why = m.guards(False, {"id": "LONG", "est_minutes": m.PIPELINE_LONG_MIN})
    chk("B 파이프라인 중 장시간 거부", ok, False)
    chk("B 사유가 파이프라인", why.startswith("저녁 파이프라인 실행 중(pid=4242"), True)

    ok, _ = m.guards(False, {"id": "SHORT", "est_minutes": m.PIPELINE_LONG_MIN - 1})
    chk("B 파이프라인 중 짧은 항목 통과", ok, True)

    ok, _ = m.guards(True, {"id": "LONG", "est_minutes": 130})
    chk("B --force 는 통과(런처 강행 보존)", ok, True)

    ok, _ = m.guards(False, {"id": "CG120", "est_minutes": 130})
    chk("B 장시간 항목 자체는 통과(대조)", ok, False)  # 파이프라인 중이므로 거부가 정답

    # 재생성 창은 파이프라인보다 먼저 걸린다(사유 우선순위 회귀)
    m.now_kst = lambda: dt.datetime(2026, 10, 5, 20, 5, tzinfo=KST)
    ok, why = m.guards(False, {"id": "LONG", "est_minutes": 130})
    chk("B 재생성 창이 우선", (ok, why.startswith(f"컨테이너 재생성 창({m.RECREATE_HOUR:02d}:00")),
        (False, True))

    # 파이프라인이 없으면(장외) 장시간 항목도 통과
    m.now_kst = lambda: dt.datetime(2026, 10, 5, 22, 0, tzinfo=KST)
    m.pipeline_in_flight = lambda: (None, None)
    ok, why = m.guards(False, {"id": "LONG", "est_minutes": 130})
    chk("B 파이프라인 없으면 통과", (ok, why), (True, "ok"))

    # est 없는 항목은 판단하지 않는다(보수적으로 막지 않되 판정 근거도 남기지 않는다)
    m.pipeline_in_flight = lambda: (4242, "full_pipeline_dd.sh")
    ok, _ = m.guards(False, {"id": "NOEST"})
    chk("B est 없으면 통과", ok, True)
finally:
    (m.running_pid, m.peer_running, m.container_up, m.load1, m.market_hours,
     m.now_kst, m.pipeline_in_flight) = saved

# ── C) 실제 /proc 스캔 인스턴트 체크 ──────────────────────────────────────────
pid, name = m.pipeline_in_flight()
print(f"INFO 실제 pipeline_in_flight() = {(pid, name)}")
chk("C 반환형", isinstance(pid, (int, type(None))) and isinstance(name, (str, type(None))), True)
if pid:
    chk("C 반환 pid 실재", os.path.exists(f"/proc/{pid}"), True)
    chk("C 이름 인식", name in m.PIPELINE_SCRIPTS, True)

print()
if fails:
    print(f"FAIL {len(fails)}건: {fails}")
    sys.exit(1)
print(f"ALL PASS ({len(cases) + 11} 검사)")
