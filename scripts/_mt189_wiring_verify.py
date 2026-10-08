#!/usr/bin/env python3
"""MT189 오프라인 검증 — 현행(결함) vs 패치본(수리)을 **한 파일에서** 대조한다.

trader-agent 파일은 **수정하지 않는다**: 패치를 임시 사본에만 적용해 import 하고,
현행은 원본 경로에서 import 해 비교한다(실주문·KIS 호출 없음).

검증 항목(기대)
  ① 현행 RunnerConfig().screeners == ["close","swing"]        ← 결함 재현
  ② 현행 r1_min_avg_score 에 daytrading 키 없음(게이트 없음)   ← 미배선 증거
  ③ engine_config().screener_names == 런너 screeners (덮어쓰기 기제)
  ④ trader_core 기본값은 3종(close,swing,daytrading)
  ⑤ 패치본 screeners == 3종                                     ← 수리
  ⑥ 패치본 engine_config().screener_names == 3종                ← 수리(엔진 도달)
  ⑦ strategy_params.json 의 screeners 로도 배선 가능(런타임 경로)
  ⑧ apply_params 는 r1_min_avg_score 를 **통째 교체** → 부분 dict 는 close/swing 게이트를 지운다
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path('/mnt/c/Users/jhshi/analyist_dd/trader-agent')
PATCH = pathlib.Path('/home/jhshi/analyist_dd/data/reports/mt189_daytrading_wiring.patch')

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append(bool(cond))
    print('%s - %s %s' % ('PASS' if cond else 'FAIL', name, detail))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    # dataclasses 가 cls.__module__ 로 sys.modules 를 조회한다 — exec 전에 등록해야 한다.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    sys.path.insert(0, str(REPO))
    import runner.config as rc  # 현행

    cur = rc.RunnerConfig()
    check('① 현행 screeners == [close,swing] (결함 재현)', cur.screeners == ['close', 'swing'], str(cur.screeners))
    check('② 현행 r1_min_avg_score 에 daytrading 없음(게이트 없음)',
          'daytrading' not in cur.r1_min_avg_score, str(cur.r1_min_avg_score))
    ec = cur.engine_config()
    check('③ engine_config().screener_names == 런너 screeners (덮어쓰기 기제)',
          list(ec.screener_names) == cur.screeners, str(list(ec.screener_names)))
    check('④ trader_core 기본값은 3종', list(rc.Config().screener_names) == ['close', 'swing', 'daytrading'],
          str(list(rc.Config().screener_names)))

    # 패치 적용(임시 사본 전용). ① 실제 파일에 클린 적용되는지 --check 로 증명(수정 없음)
    r = subprocess.run(['git', 'apply', '--check', '-p1', str(PATCH)],
                       cwd=str(REPO), capture_output=True, text=True)
    check('패치가 실제 runner/config.py 에 클린 적용(git apply --check)',
          r.returncode == 0, r.stderr.strip())

    # ② 임시 사본은 패치와 동일한 앵커 치환으로 만든다(패치 본문과 일치를 assert)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='mt189_'))
    (tmp / 'runner').mkdir(parents=True)
    src = (REPO / 'runner/config.py').read_text(encoding='utf-8')
    ptxt = PATCH.read_text(encoding='utf-8')
    new_field = '    screeners: List[str] = field(default_factory=lambda: ["close", "swing", "daytrading"])'
    check('패치 본문이 3-스크리너 기본값을 실제로 추가한다', ('+' + new_field) in ptxt)
    (tmp / 'runner' / 'config.py').write_text(
        src.replace('    screeners: List[str] = field(default_factory=lambda: ["close", "swing"])',
                    new_field),
        encoding='utf-8')

    pc = load_module(tmp / 'runner' / 'config.py', 'runner_config_patched')
    patched = pc.RunnerConfig()
    check('⑤ 패치본 screeners == [close,swing,daytrading]', patched.screeners == ['close', 'swing', 'daytrading'],
          str(patched.screeners))
    check('⑥ 패치본 engine_config().screener_names == 3종',
          list(patched.engine_config().screener_names) == ['close', 'swing', 'daytrading'],
          str(list(patched.engine_config().screener_names)))

    # 런타임 파라미터 경로
    p = tmp / 'strategy_params.json'
    p.write_text(json.dumps({'screeners': ['close', 'swing', 'daytrading']}), encoding='utf-8')
    alt = rc.RunnerConfig().apply_params(str(p))
    check('⑦ strategy_params.json 의 screeners 로도 배선 가능(런타임 대안)',
          alt.screeners == ['close', 'swing', 'daytrading'], str(alt.screeners))
    p.write_text(json.dumps({'r1_min_avg_score': {'daytrading': 60.0}}), encoding='utf-8')
    par = rc.RunnerConfig().apply_params(str(p))
    check('⑧ 부분 dict 는 r1_min_avg_score 를 통째 교체(close/swing 게이트 소실 — 함정)',
          par.r1_min_avg_score == {'daytrading': 60.0}, str(par.r1_min_avg_score))

    ok = sum(1 for x in RESULTS if x)
    print('\nMT189 wiring verify: %d/%d PASS' % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
