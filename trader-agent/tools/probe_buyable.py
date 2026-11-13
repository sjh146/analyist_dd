"""CpTdNew5331A(계좌별 매수 가능금액/수량) 입력 조합 프로브.

용도: /balance 가 `buyable_error: 12275 종목코드를 확인하십시요
(ord.stk.dpmyinqr.selpsblqtyamt)` 로 현금(cash)을 못 받아오는 상태에서,
어떤 입력 조합이 금액조회로 성공하는지 실계좌에서 직접 확인한다.

실행 (Creon PLUS 로그인 상태, 32비트):
    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\probe_buyable.py

읽기 전용 조회 4건. 성공 조합의 header 10/45/47 값을 출력한다.
"""
import sys
import traceback

print("bits:", 64 if sys.maxsize > 2 ** 32 else 32, flush=True)

import pythoncom  # noqa: E402
import win32com.client  # noqa: E402

pythoncom.CoInitialize()

try:
    util = win32com.client.Dispatch("CpTrade.CpTdUtil")
    init = util.TradeInit(0)
    print("TradeInit:", init, flush=True)
    account = util.AccountNumber[0]
    goods = util.GoodsList(account, 1)[0]
    print("account:", account, "goods:", goods, flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(1)

cybos = win32com.client.Dispatch("CpUtil.CpCybos")
print("IsConnect:", cybos.IsConnect, "ServerType:", cybos.ServerType, flush=True)

# (라벨, {인덱스: 값})
VARIANTS = [
    ("A 현재구현: code=''  hogagubun='01' johoe='1'", {2: "", 3: "01", 6: "1"}),
    ("B 금액조회 johoe='0'", {2: "", 3: "01", 6: "0"}),
    ("C 종목코드 지정 johoe='1'", {2: "A005930", 3: "01", 6: "1"}),
    ("D 종목코드 지정 johoe='0'", {2: "A005930", 3: "01", 6: "0"}),
]

for label, inputs in VARIANTS:
    print("\n=== " + label, flush=True)
    try:
        obj = win32com.client.Dispatch("CpTrade.CpTdNew5331A")
        obj.SetInputValue(0, account)
        obj.SetInputValue(1, goods)
        for idx, val in inputs.items():
            obj.SetInputValue(idx, val)
        ret = obj.BlockRequest()
        status = obj.GetDibStatus()
        msg = obj.GetDibMsg1() if status != 0 else ""
        print("BlockRequest ret={0} status={1} msg={2}".format(ret, status, msg), flush=True)
        if ret == 0 and status == 0:
            for h in (0, 5, 10, 45, 46, 47):
                try:
                    print("  header[{0}] = {1}".format(h, obj.GetHeaderValue(h)), flush=True)
                except Exception as exc:  # noqa: BLE001
                    print("  header[{0}] ERR {1}".format(h, exc), flush=True)
    except Exception:
        traceback.print_exc()
    finally:
        try:
            obj = None
        except Exception:
            pass

print("\nDONE", flush=True)
