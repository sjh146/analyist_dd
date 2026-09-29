#!/usr/bin/env python3
"""u3_done_check.py — U3(995일 패널 + 로버스트 스윕)가 **결과까지** 끝났는지 판정한다.

왜 필요한가 (실측 2026-09-29 22:5x):
  u3_launcher.sh 의 종료 조건이 `[ -f panel_995.npz ]` 였다. 그런데 패널은 빌드 **끝**에 저장되고
  스윕은 그 **뒤**에 시작되므로, 개장 전 컨테이너 timeout(08:35:55)에 스윕이 잘려 죽는 밤에는
  패널만 남고 결과가 없다 → 런처가 스스로 종료 → 틱은 est 1410분 때문에 ETA 가드로 U3 를 계속
  건너뛴다(장시간 항목은 틱이 착수할 수 없다). 즉 **스윕을 아무도 시작하지 않는 교착**이 생긴다.
  패널 npz 가 캐시되면(wf_wave.build_panel 이 `panel cache 재사용` 경로) 재실행 비용은 스윕뿐이므로,
  런처는 '패널 존재'가 아니라 'U3 rc=0 기록'을 보고 종료해야 한다.

출력: 1 = 완료(런처 종료 가능) / 0 = 미완료(런처 유지)
종료코드: 항상 0 (판정은 stdout 으로만 전달 — 셸이 `$(...)` 로 읽는다)
"""
import argparse
import json
import os
import sys

DEFAULT_LEDGER = "/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl"


def u3_done(ledger=DEFAULT_LEDGER, item_id="U3"):
    """원장에 해당 id 의 rc==0 기록이 하나라도 있으면 True."""
    try:
        with open(ledger, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue          # 잘린 줄은 무시(원장은 append-only jsonl)
                if rec.get("id") == item_id and rec.get("rc") == 0:
                    return True
    except FileNotFoundError:
        return False
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="U3 결과 완료 여부(1/0)")
    ap.add_argument("--ledger", default=os.environ.get("U3_LEDGER", DEFAULT_LEDGER))
    ap.add_argument("--id", default="U3")
    a = ap.parse_args(argv)
    print(1 if u3_done(a.ledger, a.id) else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
