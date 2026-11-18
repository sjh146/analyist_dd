#!/usr/bin/env python3
"""rebuild_panel — 죽은 피처 부활 1단계: 최신 소스 기준으로 같은 200종목 패널을 새 파일로 재빌드.

스펙: docs/spec_panel_rebuild.md (작성 2026-11-18). 사용자 질문 "죽은 피처는 왜 생긴 거지?"
→ 원인 4가지 확인 후 1번(패널 재빌드)부터 실행. 목표는 **최신 소스 기준으로 같은 200종목
패널을 새 파일로 다시 만들어**, 죽은 피처 중 얼마가 "데이터가 없어서" 죽었는지 "빌더가
0으로 덮어서" 죽었는지를 분리 측정하는 것.

WHY — 실측 근거 (2026-11-18, 아래 조회 명령으로 직접 확인한 값):
  1) 현행 패널 `services/xgboost-ml/app/models/wf/panel_prod200.npz` 계측 (호스트 /usr/bin/python3):
       /usr/bin/python3 -c "import numpy as np; p=np.load(
           'services/xgboost-ml/app/models/wf/panel_prod200.npz', allow_pickle=True);
           X=p['X']; print(X.shape, len(p['feature_names']), len(set(p['codes'])),
           len(set(p['dates'])), p['dates'].min(), p['dates'].max());
           nnz=(X!=0).sum(axis=0); print('const0', (nnz==0).sum(), 'nnz<5%', (nnz<0.05*len(X)).sum())"
       → X (54800, 213) float32 · 피처 213 · 종목 200 · 날짜 279 ·
         구간 2025-08-04 ~ 2026-09-23 · 전행 0 피처 24개 · nnz<5% 피처 43개
       (파일 mtime 2026-10-03 03:25 KST = 2026-10-02T18:25 UTC — 패널은 2026-10-02 빌드)
  2) 피처 생존 판정 data/reports/feature_survival.json:
       /usr/bin/python3 -c "import json; d=json.load(open('data/reports/feature_survival.json'));
           print(d['summary'])"
       → total 213 = alive 117 / dead 69 (DEAD_CONST_ZERO 24 + DEAD_SPARSE 45) /
         조건부 27 (CONST_MARKET 24 + LEAK_SUSPECT 3)
  3) 원천 테이블 시계 (DB 읽기 전용 SELECT, docker exec stock_postgres psql -U stock_user
     -d stock_trading -t -c "...min(created_at)::date, max(created_at)::date, count(*)...")
       news_events        2026-04-01 ~ 2026-11-18 (12,817행)
       sns_post_features  2026-09-23 ~ 2026-11-13 (18,807행)
       stock_sentiment    2026-11-15 ~ 2026-11-18 (220행)
     → 패널 구간 끝(2026-09-23) 기준으로 SNS 는 마지막 날, 센티먼트는 0일만 겹친다.
  4) 패널 200종목 기준 원천 커버리지 data/reports/source_coverage_sweep.json:
       /usr/bin/python3 -c "import json; d=json.load(open('data/reports/source_coverage_sweep.json'));
           [print(t['table'], t['panel_cover_n'], t['panel_cover_frac']) for t in d['tables']]"
       → news_events 27/200(0.135) · sns_post_features 29/200(0.145) ·
         foreign_institutional 91/200(0.455) · event_features 191/200(0.955)
  즉 **패널이 만들어진 시점의 소스 공백**이 dead 의 큰 원인 후보(H1)다. 이 스크립트는
  `scripts/wf_wave.py` 의 `build_panel()` 을 **import 해서 그대로** 호출하고(빌드 로직 복제 금지 —
  정의가 갈라지면 기준선과의 비교가 무의미해진다), 완성 패널의 실측값(행수·날짜·피처수·종목 지문)을
  sidecar/JSON 으로 남겨 H1 을 계량한다.

안전 규약(스펙 §4):
  · 기준선 패널(panel_prod200.npz, panel_420.npz)은 **basename 만으로** 거부한다(exit 2) —
    대조군 보존. 재빌드 결과는 반드시 새 파일명으로 쓴다.
  · 기존 스크립트(wf_wave.py 등)는 수정하지 않는다(import 만).
  · DB 는 SELECT 전용(build_panel 내부의 읽기뿐). 컨테이너 재시작 금지.
  · 호스트 실행은 거부하고 컨테이너(stock_xgboost_ml, /app 마운트)의 docker exec 명령을
    안내한다(exit 3) — 파이썬 의존성 부재 시 traceback 금지. `--dry-run` 은 호스트에서도
    허용(빌드 없이 인자·경로·컨테이너 실행 명령만 출력).

멱등성(재실행 안전):
  · sidecar/--json-out 은 **덮어쓰기(원자적 교체)** — 재실행해도 같은 실측값으로 다시 쓰인다.
  · 패널 파일이 이미 있으면 build_panel 은 재사용(덮어쓰기 없음)하고, 리포트에 reused=true 로
    남겨 "재빌드가 아니라 재사용"임을 숨기지 않는다(조용한 재사용 = CG199 사고 유형).
  · 자기신고(dq_runner_claim INSERT)는 실패해도 재빌드를 깨지 않는다(dq_claim 설계 원칙).

자기신고 3분리(dq_claim.record_claim 재사용, 규약 = scripts/_authoring_probe_smoke.py·audit_safety.py):
  source_rows   = build_panel 이 반환한 df 행수(소스=DB→파이프라인 수신)
  claimed_rows  = 패널 df 행수(파서 생성 — 이 러너의 파서는 identity, sidecar 에 기록한 값)
  persisted_rows= 빌드 후 npz 를 디스크에서 **다시 읽어** 센 X 행수(실제 저장)

사용(컨테이너):
  docker exec stock_xgboost_ml python -u scripts/rebuild_panel.py \
    --panel-out /app/app/models/wf/panel_rebuild_<tag>.npz --days 470 --end-date 2026-11-17
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

# 기준선 패널 — basename 이 이것과 같으면 거부(exit 2). 대조군 보존(스펙 §1/§4).
# 실측: services/xgboost-ml/app/models/wf/ 아래에 panel_prod200.npz(2026-10-02 빌드)와
# panel_420.npz(2026-09-25 빌드)가 현존한다(ls 로 확인).
BASELINE_PANELS = {"panel_prod200.npz", "panel_420.npz"}

_END_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse(argv):
    p = argparse.ArgumentParser(
        prog="rebuild_panel.py",
        description="최신 소스 기준으로 같은 200종목 패널을 새 파일로 재빌드(wf_wave.build_panel 재사용). "
                    "기준선 패널(panel_prod200.npz, panel_420.npz) basename 은 거부된다(exit 2).",
        epilog="컨테이너에서 실행하라: docker exec stock_xgboost_ml python -u scripts/rebuild_panel.py "
               "--panel-out /app/app/models/wf/panel_rebuild_<tag>.npz --days 470 --end-date 2026-11-17")
    p.add_argument("--panel-out", required=True,
                   help="새 패널 파일 경로(필수). basename 이 기준선 패널과 같으면 exit 2.")
    p.add_argument("--days", type=int, default=470,
                   help="빌드 구간 일수(달력일). 기본 470.")
    p.add_argument("--end-date", required=True,
                   help="빌드 구간 끝 날짜 YYYY-MM-DD(필수 — 구간 고정이 재현성의 전제, wf_wave 의 "
                        "end_date 파라미터와 동일).")
    p.add_argument("--limit", type=int, default=200, help="패널 종목수. 기본 200.")
    p.add_argument("--universe", choices=["prod", "curated"], default="prod",
                   help="유니버스 선택 규칙. prod = 프로덕션 select_training_universe(기본), "
                        "curated = 실험용 KOSDAQ 큐레이션.")
    p.add_argument("--universe-seed", type=int, default=0, help="prod 유니버스 셔플 seed. 기본 0.")
    p.add_argument("--dry-run", action="store_true",
                   help="빌드 없이 인자·경로·컨테이너 실행 명령만 출력하고 종료(호스트에서도 허용).")
    p.add_argument("--json-out", default="reports/panel_rebuild.json",
                   help="빌드 조건 JSON 출력 경로. 기본 reports/panel_rebuild.json (상대경로는 "
                        "실행 cwd 기준).")
    return p.parse_args(argv)


def _fail(msg, code=2):
    print(f"rebuild_panel: {msg}", file=sys.stderr)
    return code


def _valid_end_date(s):
    if not _END_DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _to_container_path(p):
    """호스트 경로 → 컨테이너(/app) 경로 안내용 변환(드라이 런/호스트 에러 메시지 전용)."""
    p = str(p).replace("\\", "/")
    if p.startswith("/app/"):
        return p
    marker = "services/xgboost-ml/"
    if marker in p:
        return "/app/" + p.split(marker, 1)[1]
    if not p.startswith("/"):
        return "/app/" + p
    return p


def docker_exec_command(args):
    """컨테이너 실행 명령(스펙 §1: 호스트 실행 시 이 명령을 안내한다)."""
    parts = ["docker", "exec", "-w", "/app", "stock_xgboost_ml",
             "python", "-u", "scripts/rebuild_panel.py",
             "--panel-out", _to_container_path(args.panel_out),
             "--days", str(args.days), "--limit", str(args.limit),
             "--end-date", args.end_date, "--universe", args.universe,
             "--universe-seed", str(args.universe_seed),
             "--json-out", _to_container_path(args.json_out)]
    return " ".join(parts)


def in_container():
    """컨테이너 안인가 — /.dockerenv 존재 여부(리포 관례, dq_claim._conn_target 과 동일 기준)."""
    return os.path.exists("/.dockerenv")


def build_report(days, end_date, universe, universe_seed, limit, rows,
                 first_date, last_date, n_features, codes, panel_path,
                 built_at=None, reused=False):
    """빌드 조건 리포트 dict(스펙 §1 스키마) — 순수 함수(테스트가 직접 검증한다).

    codes_sha1 = sha1(정렬된 고유 종목코드를 콤마 결합) — 행 순서 무관의 **종목 구성** 지문.
    기준선(panel_prod200.npz)과 재빌드 패널이 '같은 200종목'인지 비교하는 데 쓴다.
    """
    if built_at is None:
        built_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    uniq = sorted({str(c) for c in codes})
    return {
        "days": int(days),
        "end_date": str(end_date),
        "universe": str(universe),
        "universe_seed": int(universe_seed),
        "limit": int(limit),
        "rows": int(rows),
        "dates": [str(first_date), str(last_date)],
        "n_features": int(n_features),
        "codes_sha1": hashlib.sha1(",".join(uniq).encode("utf-8")).hexdigest(),
        "panel_path": os.path.abspath(str(panel_path)),
        "built_at": built_at,
        "reused": bool(reused),
    }


def _write_json(path, report):
    """report dict 를 원자적으로 쓴다(임시 파일 → os.replace = 멱등 덮어쓰기, 반쯤 쓰인 파일 없음)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rebuild_panel_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        # mkstemp 는 0600 으로 만든다 — 컨테이너(root) 실행 시 호스트 사용자가 sidecar/json 을
        # 못 읽어 스펙 §2 검증(호스트에서 sidecar 읽기)이 PermissionError 로 죽는다(실측 2026-11-18).
        # 기존 패널 파일과 같은 0644 로 맞춘다.
        os.chmod(path, 0o644)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _build(cache, limit, days, end_date, universe, universe_seed, log=print):
    """wf_wave.build_panel 을 지연 import 로 호출한다 — 빌드 로직 복제 금지(스펙 §1).

    지연 import 인 이유: wf_wave 는 모듈 import 시 드라이버 로드(ex._load_driver)와
    /app 경로 의존이 있어 호스트에서는 import 자체가 죽는다. 이 스크립트는 호스트에서
    인자 검증·드라이 런·안내 메시지까지는 **표준 라이브러리만으로** 돌아야 한다.
    테스트는 이 함수를 monkeypatch 해 '빌드 호출 여부'를 감시한다(스펙 §3-③).
    """
    sys.path.insert(0, "/app/scripts")  # 컨테이너 마운트(리포 scripts/) — wf_wave/panel_meta 경로
    import wf_wave  # noqa: PLC0415
    return wf_wave.build_panel(cache, limit, days, log=log, end_date=end_date,
                               universe=universe, universe_seed=universe_seed)


def _disk_rows(panel_out):
    """디스크에 실제로 저장된 npz 의 X 행수를 다시 읽어 센다(자기신고 persisted_rows 용)."""
    import numpy as np  # noqa: PLC0415 — 호스트 검증 경로는 이 함수에 닿지 않는다
    z = np.load(panel_out, allow_pickle=True)
    return int(z["X"].shape[0])


def _record_claim(panel_path, rows, disk_rows, reused, n_features):
    """1회 실행의 자기신고(3분리)를 dq_runner_claim 에 남긴다.

    source_rows=rows   : build_panel 이 반환한 df 행수(소스 수신 = DB→파이프라인 산출물)
    claimed_rows=rows  : 패널 df 행수(파서 생성 — 이 러너의 파서는 identity)
    persisted_rows     : npz 를 디스크에서 다시 읽어 센 행수(실제 저장)
    자기신고 실패가 재빌드를 깨면 안 된다(dq_claim 설계 원칙) — 예외는 삼키고 stderr 로만 알린다.
    """
    try:
        from dq_claim import _open_conn, record_claim  # noqa: PLC0415 — psycopg2 지연 import 규약
        conn = _open_conn()
        try:
            record_claim(conn, runner="panel_rebuild", table_name="panel_rebuild",
                         source_rows=rows, claimed_rows=rows, persisted_rows=disk_rows,
                         note=f"panel={panel_path} reused={reused} n_features={n_features} "
                              f"disk_ok={1 if disk_rows == rows else 0}")
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 의도: 자기신고 실패는 치명 오류가 아니다
        print(f"⚠ panel_rebuild 자기신고 실패({type(e).__name__}: {e}) — 빌드 결과는 유효",
              file=sys.stderr)


def main(argv=None):
    args = _parse(argv)

    # ── 인자 검증(exit 2) — 호스트/컨테이너 구분보다 먼저. 형식 오류는 어디서든 같은 결과여야 한다.
    if not _valid_end_date(args.end_date):
        return _fail(f"--end-date 형식 오류: {args.end_date!r} (YYYY-MM-DD, 예 2026-11-17)")
    if args.days < 1:
        return _fail(f"--days 는 1 이상이어야 한다: {args.days}")
    if args.limit < 1:
        return _fail(f"--limit 는 1 이상이어야 한다: {args.limit}")

    # ── 대조군 보존: 기준선 패널 basename 거부(exit 2)
    if os.path.basename(os.path.abspath(args.panel_out)) in BASELINE_PANELS:
        return _fail(f"기준선 패널 basename 은 거부된다(대조군 보존): {args.panel_out} "
                     f"— 새 파일명을 쓰라 (예: /app/app/models/wf/panel_rebuild_<tag>.npz)")

    if args.dry_run:
        print("[dry-run] 빌드 없이 인자·경로·컨테이너 실행 명령만 출력 (빌드 호출 없음)")
        print(f"[dry-run] panel-out(컨테이너): {_to_container_path(args.panel_out)}")
        print(f"[dry-run] sidecar          : {_to_container_path(args.panel_out)}.sidecar.json")
        print(f"[dry-run] json-out(컨테이너): {_to_container_path(args.json_out)}")
        print(f"[dry-run] days={args.days} limit={args.limit} end-date={args.end_date} "
              f"universe={args.universe} universe-seed={args.universe_seed}")
        print("[dry-run] 컨테이너 실행 명령:")
        print("  " + docker_exec_command(args))
        return 0

    # ── 실행 환경: 컨테이너(stock_xgboost_ml) 전제. 호스트면 docker exec 명령 안내로 끝낸다(exit 3).
    if not in_container():
        print("rebuild_panel: 패널 빌드는 wf_wave(드라이버 로드·DB 연결)에 의존하므로 호스트에서 "
              "실행하지 않는다 — 컨테이너(stock_xgboost_ml) 안에서 실행하라. 다음 명령을 쓰라:",
              file=sys.stderr)
        print("  " + docker_exec_command(args), file=sys.stderr)
        return 3

    log = lambda m: print(m, flush=True)  # noqa: E731 — build_panel(log=) 계약
    existed = os.path.exists(args.panel_out)
    log(f"rebuild_panel 시작: {args.panel_out} "
        f"(days={args.days} limit={args.limit} end-date={args.end_date} "
        f"universe={args.universe} seed={args.universe_seed})")
    if existed:
        log(f"⚠ 대상 파일이 이미 존재한다 — build_panel 은 재사용 경로로 돌고(덮어쓰기 없음), "
            f"리포트에 reused=true 로 남긴다. 새 빌드를 원하면 다른 파일명을 쓰라.")

    df, names = _build(cache=args.panel_out, limit=args.limit, days=args.days,
                       end_date=args.end_date, universe=args.universe,
                       universe_seed=args.universe_seed, log=log)
    rows = int(len(df))
    first, last = str(df["date"].min()), str(df["date"].max())
    report = build_report(args.days, args.end_date, args.universe, args.universe_seed,
                          args.limit, rows, first, last, len(names),
                          df["stock_code"].astype(str), args.panel_out, reused=existed)

    sidecar = args.panel_out + ".sidecar.json"
    _write_json(sidecar, report)
    log(f"sidecar 저장: {sidecar}")
    _write_json(args.json_out, report)
    log(f"json-out 저장: {args.json_out}")

    disk_rows = _disk_rows(args.panel_out)
    log(f"실제 저장 검증: npz X.shape[0]={disk_rows} (df rows={rows}, 일치={disk_rows == rows})")
    if disk_rows != rows:
        print(f"⚠ disk rows({disk_rows}) != df rows({rows}) — 자기신고 gap 으로 기록된다",
              file=sys.stderr)

    _record_claim(args.panel_out, rows, disk_rows, existed, len(names))

    log(f"완료: rows={rows} dates=[{first},{last}] n_features={len(names)} "
        f"codes={df['stock_code'].nunique()} codes_sha1={report['codes_sha1']} "
        f"reused={existed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
