#!/usr/bin/env python3
"""KRX OpenAPI 승인/응답 진단 — 정확히 1콜만 호출한다.

여러 헤더/호스트/경로 변형을 시도해보는 행동은 이 IP대역의 과거 차단 사유다.
이 스크립트는 후보 호스트를 순서대로 보되 **JSON(=OpenAPI) 응답이 오는 순간 멈추고**
그 결과만으로 진단한다.

사용:
  KRX_API_KEY=... python3 krx_probe.py                       # 기본: 유가증권 일별매매정보
  python3 krx_probe.py --env /home/USER/proj/.env --service sto/ksq_bydd_trd
  python3 krx_probe.py --date 20260918

종료코드: 0=정상(데이터 수신) / 2=키 없음 / 3=키는 있으나 서비스 미승인·차단 / 1=기타
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

HOSTS = [
    "https://data-dbg.krx.co.kr/svc/apis",   # JSON 을 돌려주는 쪽 (실측)
    "https://data.krx.co.kr/svc/apis",       # 운영 호스트 — 같은 경로가 404 인 경우가 있다
]


def load_env(path):
    """KEY=VALUE .env 를 환경변수로 올린다(이미 설정된 값 우선)."""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError as e:
        print(f".env 읽기 실패({path}): {e}", file=sys.stderr)


def call(url, key):
    """1콜. (http_code, body_text) 반환 — 예외도 응답으로 바꿔 진단에 쓴다."""
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"transport error: {e}"


def main():
    ap = argparse.ArgumentParser(description="KRX OpenAPI 1콜 진단")
    ap.add_argument("--env", default="", help="KEY=VALUE .env 경로 (KRX_API_KEY)")
    ap.add_argument("--service", default="sto/stk_bydd_trd",
                    help="유가증권 sto/stk_bydd_trd · 코스닥 sto/ksq_bydd_trd · 지수 idx/kospi_dd_trd")
    ap.add_argument("--date", default="", help="YYYYMMDD (기본: 3일 전 — KRX는 당일 데이터를 주지 않는다)")
    args = ap.parse_args()

    load_env(args.env)
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("KRX_API_KEY 없음 — 키를 넣은 뒤 재실행")
        return 2
    basdd = args.date or (date.today() - timedelta(days=3)).strftime("%Y%m%d")

    last = None
    for host in HOSTS:
        url = f"{host}/{args.service}?basDd={basdd}"
        code, body = call(url, key)
        last = (host, code, body)
        print(f"[{host}] http={code} {body.strip()[:110]}")
        if body.strip().startswith("{"):
            break  # OpenAPI 가 응답했다 → 여기서 멈춘다 (추가 호출 금지)

    host, code, body = last
    try:
        data = json.loads(body)
    except ValueError:
        print("진단: JSON 아님 — 호스트/경로가 다르거나 WAF 차단(HTML). 호스트 후보를 확인한다")
        return 3

    resp = str(data.get("respCode", ""))
    msg = data.get("respMsg", "")
    rows = data.get("OutBlock_1") or []
    if code == 200 and resp in ("200", "0") and rows:
        head = rows[0]
        print(f"진단: 정상 — {len(rows)}행, 예: {head.get('ISU_CD')} {head.get('ISU_NM')} "
              f"{head.get('TDD_CLSPRC')}")
        return 0
    if resp == "401" or code == 401:
        if "Unauthorized Key" in msg:
            print("진단: 키가 인식되지 않음(발급/오타/만료 확인)")
        else:
            print("진단: 키는 유효하지만 이 서비스에 승인되지 않음 → "
                  "openapi.krx.co.kr 마이페이지에서 API 활용신청(서비스별, 승인 1일 내외)")
        return 3
    if code == 403:
        print("진단: WAF/IP 차단 — 재시도 금지, 다른 소스로 피벗")
        return 3
    if code == 200 and not rows:
        print(f"진단: 200이지만 행 0건 — 휴장일 수 있다(respMsg={msg!r}). 날짜를 영업일로 바꿔 재확인")
        return 1
    print(f"진단: 예상 밖 응답 (http={code}, respCode={resp}, msg={msg!r})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
