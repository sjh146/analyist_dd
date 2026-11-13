#!/usr/bin/env python3
"""bridge /balance 의 total_eval_pnl 이 (총평가-총매입)과 일치하는지 실측하는 프로브.

T43 재현 명령. 출력은 **차이(원) 하나**뿐이다 — trader_cycle 의 check 파서가
stdout 의 마지막 수치를 비교하기 때문.

WSL 에서 127.0.0.1:8100 은 Windows 루프백이라 직접 못 붙는다(그리고 이 셸에는
HTTP_PROXY 가 걸려 있다) → cmd.exe 로 Windows curl 을 돌려 JSON 만 뽑는다.
"""
import json
import subprocess
import sys

URL = "http://127.0.0.1:8100/balance"
CMD = 'cd /d C:\\Users\\jhshi && curl -s --max-time 8 {0}'.format(URL)


def main() -> int:
    try:
        p = subprocess.run(["cmd.exe", "/c", CMD], capture_output=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print("0")
        print("bridge call failed: {0}".format(exc), file=sys.stderr)
        return 1
    out = (p.stdout or b"").decode("utf-8", "replace")
    i = out.find("{")
    if i < 0:
        print("0")
        print("no json in output rc={0}: {1}".format(p.returncode, out[:200]), file=sys.stderr)
        return 1
    payload = json.loads(out[i:])
    bal = payload.get("balance") or payload
    calc = float(bal.get("total_eval_amount") or 0) - float(bal.get("total_buy_amount") or 0)
    claimed = float(bal.get("total_eval_pnl") or 0)
    diff = abs(claimed - calc)
    print("bridge total_eval_pnl={0} calc={1} diff={2}".format(claimed, calc, diff), file=sys.stderr)
    print("{0:.0f}".format(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
