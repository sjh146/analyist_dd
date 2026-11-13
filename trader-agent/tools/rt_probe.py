"""실시간(StockCur) 수신 프로브 — Creon PLUS 로그인 상태에서 단독 실행.

용도: 브리지와 무관하게 "이 프로세스에서 실시간 틱이 실제로 들어오는가"를
20초간 세어서 확인한다. 실시간이 안 올 때 원인을 (a) COM/로그인 문제인지
(b) 브리지 아파트/펌프 문제인지 분리하는 첫 진단 도구.

사용::

    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\rt_probe.py

기대: TICK#n price=... 줄이 여러 번 찍히고 RESULT ticks=N (N>0).
TICK 이 0건이면 Creon PLUS 로그인/모의투자 실시간 제공 여부를 먼저 확인할 것.

주의: 브리지를 통하지 않고 이 프로세스가 직접 구독한다 (구독 1건, 조회 0건).
 브리지 이슈(예: plus.py 의 idle pump 누락)는 이 프로브가 정상인데 HTTP
 /subscribe 경로만 0건일 때 의심한다 — OPERATIONS.md §4 참고.
"""
import sys
import time
import traceback

print("python:", sys.version.replace("\n", " "), flush=True)
print("bits:", 64 if sys.maxsize > 2**32 else 32, flush=True)

import pythoncom  # noqa: E402
import win32com.client  # noqa: E402

print("pywin32 import OK", flush=True)

try:
    pythoncom.CoInitialize()
    print("CoInitialize OK", flush=True)
except Exception:
    traceback.print_exc()

# --- 1) CybosPlus 연결 상태 ---
try:
    cybos = win32com.client.Dispatch("CpUtil.CpCybos")
    print("IsConnect:", cybos.IsConnect, flush=True)
    try:
        print("ServerType(0:실서버 1:모의투자 등):",
              getattr(cybos, "ServerType", "n/a"), flush=True)
    except Exception:
        traceback.print_exc()
except Exception:
    traceback.print_exc()

ticks = []
_holder = {}


class Handler:
    def OnReceived(self):
        obj = _holder.get("obj")
        try:
            ticks.append(1)
            print("TICK#%d price=%s vol=%s time=%s exflag=%s"
                  % (len(ticks), obj.GetHeaderValue(13), obj.GetHeaderValue(9),
                     obj.GetHeaderValue(18), obj.GetHeaderValue(19)), flush=True)
        except Exception:
            traceback.print_exc()


# --- 2) StockCur 생성 + WithEvents + Subscribe ---
obj = None
try:
    obj = win32com.client.Dispatch("DsCbo1.StockCur")
    print("Dispatch DsCbo1.StockCur OK:", obj, flush=True)
    _holder["obj"] = obj
except Exception:
    traceback.print_exc()

handler = None
if obj is not None:
    try:
        handler = win32com.client.WithEvents(obj, Handler)
        print("WithEvents OK", flush=True)
    except Exception:
        traceback.print_exc()

if obj is not None:
    try:
        obj.SetInputValue(0, "A005930")
        print("SetInputValue(0, A005930) OK", flush=True)
    except Exception:
        traceback.print_exc()
    try:
        obj.Subscribe()
        print("Subscribe() OK", flush=True)
    except Exception:
        traceback.print_exc()

# --- 3) 20초 펌프하며 틱 수신 ---
start = time.time()
while time.time() - start < 20:
    try:
        pythoncom.PumpWaitingMessages()
    except Exception:
        traceback.print_exc()
    time.sleep(0.02)

print("RESULT ticks=%d in %.1fs" % (len(ticks), time.time() - start), flush=True)

try:
    obj.Unsubscribe()
    print("Unsubscribe OK", flush=True)
except Exception:
    traceback.print_exc()
