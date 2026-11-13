"""CpTdNew5331A 입력 조합 2차 프로브 — 현금(예수금) 조회 성공 조합 찾기.

1차 결과:
  A code='' johoe='1'      -> 12275 종목코드를 확인하십시요
  B code='' johoe='0'      -> 12275
  C code='A005930' '1'     -> 12340 주문가능금액 확인바랍니다
  D code='A005930' '0'     -> 12340

2차: 상품구분 전체 목록 + 단가 입력 + 호가구분/조회구분 조합.

실행: C:\\Users\\jhshi\\Python311-32\\python.exe tools\\probe_buyable2.py
"""
import sys
import traceback

import pythoncom
import win32com.client

pythoncom.CoInitialize()

util = win32com.client.Dispatch("CpTrade.CpTdUtil")
print("TradeInit:", util.TradeInit(0), flush=True)
account = util.AccountNumber[0]
try:
    print("AccountNumber list:", list(util.AccountNumber), flush=True)
except Exception:
    print("AccountNumber list: (not iterable)", flush=True)
try:
    goods = util.GoodsList(account, 1)
    print("GoodsList(acc,1):", list(goods), flush=True)
except Exception as exc:  # noqa: BLE001
    print("GoodsList err:", exc, flush=True)
    goods = ["01"]

VARIANTS = []
for g in list(goods)[:3]:
    VARIANTS.append((f"E goods={g} code='' johoe='1'", g, {2: "", 3: "01", 6: "1"}))
    VARIANTS.append((f"F goods={g} code='A005930' price johoe='1'", g,
                     {2: "A005930", 3: "01", 4: 70000, 6: "1"}))
    VARIANTS.append((f"G goods={g} code='A005930' price johoe='0'", g,
                     {2: "A005930", 3: "01", 4: 70000, 6: "0"}))

for label, g, inputs in VARIANTS:
    print("\n=== " + label, flush=True)
    try:
        obj = win32com.client.Dispatch("CpTrade.CpTdNew5331A")
        obj.SetInputValue(0, account)
        obj.SetInputValue(1, g)
        for idx, val in inputs.items():
            obj.SetInputValue(idx, val)
        ret = obj.BlockRequest()
        status = obj.GetDibStatus()
        msg = obj.GetDibMsg1() if status != 0 else ""
        print("ret=", ret, "status=", status, "msg=", msg, flush=True)
        if status == 0:
            for h in (5, 10, 45, 46, 47):
                try:
                    print("  header[%d] = %s" % (h, obj.GetHeaderValue(h)), flush=True)
                except Exception as exc:  # noqa: BLE001
                    print("  header[%d] ERR %s" % (h, exc), flush=True)
    except Exception:
        traceback.print_exc()

print("\nDONE", flush=True)
