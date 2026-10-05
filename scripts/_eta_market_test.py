#!/usr/bin/env python3
"""_eta_market_test.py — eta_blocks 의 '다음 장 시작' 가드 검증.

배경(실측 2026-09-29): guards() 의 장중 가드는 **시작**만 막는다. eta_blocks 는 재생성 창
(평일 20:00)만 봤기 때문에 est_minutes 가 큰 항목이 07:00 같은 개장 직전에 시작하면
(예: 07:00 + 420분 = 14:00) 장 전체(09:00~15:30)를 점유한다. 실측 전례: 2026-09-28 05:0x
U3(est 를 '남은 시간' 700 으로 오기)가 05:0x 에 시작해 컨테이너 timeout 이 16:41 에 걸렸다.

검증 항목:
  1) next_market_open(): 당일 09:00 / 다음 평일 09:00 / 금->월 / 토->월 / 휴장일 건너뜀
  2) eta_blocks(): 개장 직전 장시간 항목 차단 · 짧은 항목 통과 · 야간 통과 · 재생성 창 회귀
  3) guards(): 락·컨테이너·부하를 stub 한 상태에서 장시간 항목이 rc=3 사유로 거부
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


def at(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=KST)


def stub_now(when):
    m.now_kst = lambda: when


print("== 1) next_market_open ==")
chk("수 07:00 -> 당일 09:00", m.next_market_open(at(2026, 9, 30, 7, 0)).strftime("%m-%d %H:%M"), "09-30 09:00")
chk("수 08:30 -> 당일 09:00", m.next_market_open(at(2026, 9, 30, 8, 30)).strftime("%m-%d %H:%M"), "09-30 09:00")
chk("수 16:00 -> 목 09:00", m.next_market_open(at(2026, 9, 30, 16, 0)).strftime("%m-%d %H:%M"), "10-01 09:00")
chk("금 16:00 -> 주말+대체휴장(10-05 개천절) 건너뛰고 화 09:00",
    m.next_market_open(at(2026, 10, 2, 16, 0)).strftime("%m-%d %H:%M"), "10-06 09:00")
chk("토 10:00 -> 주말+대체휴장 건너뛰고 화 09:00",
    m.next_market_open(at(2026, 10, 3, 10, 0)).strftime("%m-%d %H:%M"), "10-06 09:00")
# 왜 고쳤나(실측 2026-09-30): 종전 기대값은 '금→월' 이라는 달력 가정이었는데 실제
# data/krx_holidays.json 에 2026-10-03(토·개천절)·10-05(대체휴일)·10-09(한글날) 이 들어 있다
# → 구동기가 10-06 을 돌려주는 것이 **정답**이고 기대값이 낡았다(달력 데이터가 갱신되면
# 하드코딩 날짜 기대값은 빨간불을 켠다).
chk("일 10:00 -> 휴장(08-17) 건너뛰고 화 09:00",
    m.next_market_open(at(2026, 8, 16, 10, 0)).strftime("%m-%d %H:%M"), "08-18 09:00")

print("== 2) eta_blocks ==")
stub_now(at(2026, 9, 30, 7, 0))
b, why = m.eta_blocks({"est_minutes": 420})
chk("수 07:00 · est420 -> 차단", (b, "장 시작" in why), (True, True))
stub_now(at(2026, 9, 30, 7, 0))
chk("수 07:00 · est60 -> 통과", m.eta_blocks({"est_minutes": 60})[0], False)
stub_now(at(2026, 9, 30, 8, 30))
chk("수 08:30 · est60 -> 차단(종료 09:30)", m.eta_blocks({"est_minutes": 60})[0], True)
stub_now(at(2026, 9, 30, 21, 0))
chk("수 21:00 · est420 -> 통과(종료 04:00)", m.eta_blocks({"est_minutes": 420})[0], False)
stub_now(at(2026, 9, 30, 15, 35))
chk("수 15:35 · est180 -> 통과(종료 18:35)", m.eta_blocks({"est_minutes": 180})[0], False)
stub_now(at(2026, 10, 2, 21, 0))
chk("금 21:00 · est420 -> 통과(종료 토 04:00 < 월 09:00)", m.eta_blocks({"est_minutes": 420})[0], False)
stub_now(at(2026, 9, 30, 12, 0))
chk("est 없음 -> 판단 안 함", m.eta_blocks({})[0], False)
stub_now(at(2026, 9, 30, 20, 0))
chk("수 20:12(est12 종료) -> 장 시작 전이라 eta_blocks 는 통과",
    m.eta_blocks({"est_minutes": 12})[0], False)

print("== 3) guards() 통합(락/컨테이너/부하 stub) ==")
m.running_pid = lambda *a, **k: None
m.peer_running = lambda: (None, None)
m.container_up = lambda: True
m.load1 = lambda: 1.0
# 저녁 파이프라인 동시 실행 가드(2026-10-05 신설)는 이 테스트의 대상이 아니다 — 실제 파이프라인이
# 도는 시각에 테스트를 돌리면 est 420 허용 케이스가 새 가드로 거부되어 거짓 FAIL 이 난다.
# (스킬 교훈: 새 가드는 같은 커밋에서 기존 회귀 테스트에 stub 을 넣어야 거짓 빨간불이 안 뜬다)
m.pipeline_in_flight = lambda: (None, None)
stub_now(at(2026, 9, 30, 20, 0))
ok, why = m.guards(False, {"est_minutes": 12})
chk("20:00 재생성 창 -> 거부(기존 동작 회귀)", (ok, "재생성" in why), (False, True))
stub_now(at(2026, 9, 30, 20, 5))
ok, why = m.guards(True, {"est_minutes": 12})
chk("20:05 --force 도 재생성 창은 못 뚫는다(기존 설계)", ok, False)
stub_now(at(2026, 9, 30, 7, 0))
ok, why = m.guards(False, {"est_minutes": 420})
chk("07:00 장시간 항목 -> 거부", (ok, "장 시작" in why), (False, True))
stub_now(at(2026, 9, 30, 21, 0))
ok, why = m.guards(False, {"est_minutes": 420})
chk("21:00 장시간 항목 -> 허용", ok, True)
stub_now(at(2026, 9, 30, 21, 0))
m.load1 = lambda: 9.0
ok, why = m.guards(True, {"est_minutes": 420})
chk("장외 --force 는 부하 가드를 뚫는다(기존 설계 유지)", ok, True)
stub_now(at(2026, 9, 30, 11, 0))
m.load1 = lambda: 1.0
ok, why = m.guards(True, {"est_minutes": 10})
chk("장중 --force 도 장중 가드는 못 뚫는다(기존 설계 유지)", ok, False)

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {len(fails)}: {fails}")
sys.exit(1 if fails else 0)
