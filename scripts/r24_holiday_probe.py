#!/usr/bin/env python3
"""R24 프로브 — data_gap 의 '당일 휴장' 오탐 차단이 실제로 배선됐는지 **수치로** 판정한다.

읽기 전용 + KIS 국내휴장일조회 1콜. 출력 마지막 수치 = 결함 수(0 = 통과).

결함 3종(각 +1):
  A. 오늘이 KIS 기준 거래일인데 data/krx_holidays.json 에 휴장으로 기록돼 있다
  B. '마감 전 당일 확정 차단' 가드가 없거나 동작하지 않는다(HOLIDAY_CONFIRM_HHMM 토글로 검정)
  C. 당일 휴장 기록 경로에 KIS 교차확인이 배선돼 있지 않다
"""
import os
import re
import sys
from datetime import date

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import data_gap  # noqa: E402

today = date.today().isoformat()
defects = []

td = data_gap.kis_is_trading_day(today)
cal = data_gap.load_holidays()
print("[R24] 당일 휴장 오탐 프로브 (읽기전용, KIS 1콜) — 선언된 거래일 판정 기준 = data/krx_holidays.json")
print("  오늘 {0} · KIS 국내휴장일조회 거래일={1}".format(today, td))
print("  캘린더 {0}항목 · 오늘 포함={1}".format(len(cal), today in cal))

# A. 거래일이 캘린더에 휴장으로 굳어 있는가
a = 1 if (td is True and today in cal) else 0
defects.append(("A: 거래일이 캘린더에 휴장 기록", a))
print("  A. 오늘(거래일)이 캘린더에 기록 = {0}".format(a))

# B. 마감 전 당일 확정 차단 가드 (파일 상수 토글로 결정적 검정)
b = 0
try:
    keep = data_gap.HOLIDAY_CONFIRM_HHMM
    data_gap.HOLIDAY_CONFIRM_HHMM = (23, 59)
    early = data_gap.holiday_confirmable(today)          # 자정~23:59 사이면 반드시 False
    data_gap.HOLIDAY_CONFIRM_HHMM = (0, 0)
    always = data_gap.holiday_confirmable(today)         # 00:00 기준이면 반드시 True
    data_gap.HOLIDAY_CONFIRM_HHMM = keep
    if early or not always:
        b = 1
except (AttributeError, TypeError):
    b = 1
defects.append(("B: 마감 전 당일 확정 차단 가드", b))
print("  B. 마감 전 차단 가드 동작 = {0}".format("통과" if b == 0 else "실패"))

# C. 기록 경로에 KIS 교차확인이 배선돼 있는가 (소스 검사 — 러너와 같은 분기)
src = open(os.path.join(PROJ, "scripts", "data_gap.py"), encoding="utf-8").read()
block = re.search(r"if no_data:\s*\n((?:\s+.*\n)+?)\s+holidays\.add", src)
c = 0 if (block and "kis_is_trading_day" in block.group(1) and "holiday_confirmable" in block.group(1)) else 1
defects.append(("C: 당일 기록 경로 KIS 교차확인 배선", c))
print("  C. 휴장 기록 분기의 교차확인 배선 = {0}".format("있음" if c == 0 else "없음"))

total = sum(v for _, v in defects)
print("  결함 내역: " + " · ".join("{0}={1}".format(n, v) for n, v in defects))
print("DEFECTS={0}".format(total))
sys.exit(0)
