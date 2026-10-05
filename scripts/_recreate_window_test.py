#!/usr/bin/env python3
"""_recreate_window_test.py — 컨테이너 재생성 창(평일 20:00~20:12) 시작 차단 검증.

배경(실측): eta_blocks 는 '예상 종료가 재생성 창을 넘는가'만 판정하므로, 20:00 정각에 뜬
크론 틱은 8~12분짜리 실험(est_minutes=8~12)을 통과시킨다 → 그 실험은 평일 저녁 파이프라인의
`docker compose up -d` 재생성과 겹쳐 SIGKILL(137) 로 죽는다(2026-09-25 20:00:16 U1 전례).
대책: in_recreate_window() 로 창 **안**에서의 시작 자체를 막는다(--force 로도 뚫지 않음).

검증 항목:
  1) 창 경계: 19:59 False · 20:00 True · 20:11 True · 20:12 False · 20:30 False
  2) 주말(토·일)은 창이 없다(크론이 1-5 이므로 재생성 자체가 없음)
  3) guards(): 창 안이면 rc=3 사유로 거부, --force 도 거부(외부 이벤트)
  4) 창 밖이면 이 가드가 다른 사유로 새지 않는다(회귀 없음)
"""
import datetime as dt
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


# 1) 창 경계 (2026-09-28 = 월요일)
for hhmm, want in [((19, 59), False), ((20, 0), True), ((20, 11), True),
                   ((20, 12), False), ((20, 30), False)]:
    d = dt.datetime(2026, 9, 28, hhmm[0], hhmm[1], tzinfo=KST)
    chk(f"월 {hhmm[0]:02d}:{hhmm[1]:02d} in_recreate_window", m.in_recreate_window(d), want)

# 2) 주말엔 창이 없다(재생성 크론이 평일만)
for day, label in [(3, "토"), (4, "일")]:
    d = dt.datetime(2026, 10, day, 20, 5, tzinfo=KST)
    chk(f"{label} 20:05 in_recreate_window", m.in_recreate_window(d), False)

# 3) guards() — 창 안에서 거부(force 포함). 외부 사유가 먼저 걸리지 않도록 스텁한다.
saved = (m.running_pid, m.peer_running, m.container_up, m.load1)
try:
    m.running_pid = lambda *a, **k: None
    m.peer_running = lambda *a, **k: (None, None)
    m.container_up = lambda: True
    m.load1 = lambda: 0.0
    item = {"id": "T", "est_minutes": 8}
    m.now_kst = lambda: dt.datetime(2026, 9, 28, 20, 3, tzinfo=KST)
    ok, why = m.guards(False, item)
    chk("guards 창 안 rc", ok, False)
    chk("guards 창 안 사유", why.startswith(f"컨테이너 재생성 창({m.RECREATE_HOUR:02d}:00"), True)
    ok_f, why_f = m.guards(True, item)
    chk("guards --force 도 창 안 거부", ok_f, False)
    # 4) 창 밖 = 이 가드가 막지 않는다
    m.now_kst = lambda: dt.datetime(2026, 9, 28, 20, 20, tzinfo=KST)
    ok2, why2 = m.guards(True, item)
    chk("guards 창 밖 통과(force)", (ok2, why2), (True, "ok"))
finally:
    (m.running_pid, m.peer_running, m.container_up, m.load1) = saved

print(f"\n{len(fails)} FAIL / 10 checks" if fails else "\n전부 PASS (10 checks)")
sys.exit(1 if fails else 0)
