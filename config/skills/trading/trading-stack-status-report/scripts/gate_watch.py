#!/usr/bin/env python3
"""관문(gate) 감시자 — 절대시각 스케줄 + 주기 스냅샷 + JSONL 원자료 (읽기 전용).

"몇 시간 뒤에야 결론이 나는" 관문(진입·청산 창 종료, 피드 발행, 루프 마감)을 한 프로세스로 관측한다.
매 반복마다 현재 시각과 목표를 다시 비교하므로 sleep 누적·시계 드리프트에 어긋나지 않고,
모든 스냅샷은 JSONL 로 남아 자리를 비운 동안의 결과가 원자료로 남는다.

사용:
  python3 gate_watch.py \
    --out  /home/u/analyist_dd/data/reports/close_watch.jsonl \
    --gates 14:50,15:10,15:20,15:29,15:36 --tick 5 --stop-after 15:40 \
    --trader-dir /mnt/c/Users/u/analyist_dd/trader-agent \
    --wpy /mnt/c/Users/u/Python312-64/python.exe \
    --journal 'C:\\Users\\u\\analyist_dd\\trader-agent\\journal\\trade_journal.sqlite3'

규약:
  · 브리지는 Windows 루프백 전용이라 WSL 에서 직접 못 붙는다 → Windows curl.exe 로 조회하고
    `--noproxy '*'` 를 반드시 붙인다(이 셸의 http_proxy 를 물면 502/000 이 온다).
  · 저널은 루프가 WAL 을 잡고 있어 WSL sqlite 로 못 열므로 Windows 파이썬에 **C:\\ 형식 경로**를 넘긴다.
    `/mnt/c/...` 를 넘기면 예외 없이 stdout 이 빈 문자열로 돌아온다.
  · 기동은 `terminal(background=true, notify=true)`. 교체할 때는 pkill 이 아니라 pid 로 끝낸다
    (pkill -f 는 이 명령을 실행 중인 셸 자신의 명령줄과도 일치해 셸을 죽인다).
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

CURL = '/mnt/c/Windows/System32/curl.exe'

JW_CODE = r'''
import sqlite3, json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
c = sqlite3.connect(sys.argv[1], timeout=8)
out = {"open": [], "closed_today": [], "decisions_today": 0, "last_reasons": []}
for code, name, qty, price, ts, screener, algo in c.execute(
        "select code,name,qty,price,ts,screener,algo_json from trades where exit_ts is null"):
    mode = None
    try:
        mode = (json.loads(algo or '{}') or {}).get('exit_mode')
    except Exception:
        pass
    out["open"].append({"code": code, "name": name, "qty": qty, "avg": price, "ts": ts,
                        "screener": screener, "exit_mode": mode})
for code, name, qty, price, exit_ts, exit_price, reason, pnl in c.execute(
        "select code,name,qty,price,exit_ts,exit_price,exit_reason,pnl from trades "
        "where exit_ts is not null and substr(exit_ts,1,10)=date('now','+9 hours')"):
    out["closed_today"].append({"code": code, "name": name, "qty": qty, "entry": price,
                                "exit": exit_price, "reason": reason, "pnl": pnl, "exit_ts": exit_ts})
out["decisions_today"] = c.execute(
    "select count(*) from decisions where substr(ts,1,10)=date('now','+9 hours')").fetchone()[0]
for ts, code, name, score, reason in c.execute(
        "select ts,code,name,score,reason from decisions order by rowid desc limit 4"):
    out["last_reasons"].append({"ts": ts, "code": code, "name": name, "score": score, "reason": reason})
print(json.dumps(out, ensure_ascii=False))
'''


def sh(args, timeout=60):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors='replace')
        return r.stdout.strip()
    except Exception:
        return ''


def http(base, path):
    return sh([CURL, '-s', '--noproxy', '*', '-m', '20', base + path])


def journal_snapshot(wpy, db):
    try:
        return json.loads(sh([wpy, '-c', JW_CODE, db], 90))
    except Exception:
        return {}


def snapshot(a, kind):
    rec = {'ts': dt.datetime.now().isoformat(timespec='seconds'), 'kind': kind}
    try:
        rec['connected'] = json.loads(http(a.bridge, '/health')).get('connected')
    except Exception:
        rec['connected'] = None
    try:
        b = json.loads(http(a.bridge, '/balance')).get('balance', {})
        rec.update({k: b.get(k) for k in ('equity', 'cash', 'npos', 'positions_count', 'total_eval_pnl')})
    except Exception:
        rec['balance_err'] = True
    try:
        rec['positions'] = [{k: x.get(k) for k in ('code', 'name', 'qty', 'avg_price', 'eval_amount', 'eval_pnl')}
                            for x in json.loads(http(a.bridge, '/positions')).get('positions', [])]
    except Exception:
        rec['positions_err'] = True
    try:
        rec['norders'] = len(json.loads(http(a.bridge, '/orders')).get('orders', []))
    except Exception:
        rec['norders'] = None
    try:
        d = json.load(open(os.path.join(a.trader_dir, 'loop_state.json')))
        loop = d.get('loop', {})
        rec['halt'] = loop.get('halt')
        rec['last_cycle'] = (d.get('last_cycle') or {}).get('ts')
        rec['daily_closed'] = (d.get('daily') or {}).get('closed')
        if (d.get('daily') or {}).get('summary'):
            rec['daily_summary'] = d['daily']['summary']
    except Exception as e:
        rec['loop_state_err'] = str(e)[:80]
    try:
        rec['runner_tail'] = [x.strip() for x in open(os.path.join(a.trader_dir, 'runner.log'),
                                                     errors='replace').read().splitlines()[-4:]]
    except Exception:
        pass
    rec['journal'] = journal_snapshot(a.wpy, a.journal)
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'a') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def brief(rec):
    j = rec.get('journal') or {}
    keys = ('ts', 'kind', 'connected', 'equity', 'cash', 'npos', 'norders', 'halt', 'daily_closed')
    d = {k: rec.get(k) for k in keys}
    d['open'] = [(x['code'], x['qty']) for x in j.get('open', [])]
    d['closed_today'] = [(x['code'], x['reason'], x['pnl']) for x in j.get('closed_today', [])]
    d['decisions_today'] = j.get('decisions_today')
    return json.dumps(d, ensure_ascii=False)


def anomalies(rec, prev):
    out = []
    if rec.get('connected') is not True:
        out.append('bridge not connected')
    if rec.get('halt'):
        out.append('halt=true')
    if (rec.get('norders') or 0) > 0:
        out.append('open orders %s' % rec.get('norders'))
    if prev:
        if (prev.get('journal') or {}) != (rec.get('journal') or {}):
            out.append('journal changed')
        if prev.get('npos') != rec.get('npos'):
            out.append('positions changed')
    return out


def schedule(gates, tick_min, stop_after):
    now = dt.datetime.now()
    stop = now.replace(hour=stop_after[0], minute=stop_after[1], second=0, microsecond=0)
    plan = []
    t = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=tick_min - now.minute % tick_min)
    while t <= stop:
        plan.append((t, 'tick'))
        t += dt.timedelta(minutes=tick_min)
    for g in gates:
        gt = now.replace(hour=g[0], minute=g[1], second=0, microsecond=0)
        if gt > now:
            plan.append((gt, 'gate_%02d%02d' % g))
    return sorted(plan)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True, help='JSONL 스냅샷 경로')
    p.add_argument('--gates', default='', help='HH:MM,HH:MM — 관문 시각(라벨 gate_HHMM)')
    p.add_argument('--tick', type=int, default=5, help='주기 스냅샷 간격(분)')
    p.add_argument('--stop-after', default='15:40', help='이 시각 이후 종료(HH:MM)')
    p.add_argument('--bridge', default='http://127.0.0.1:8100')
    p.add_argument('--trader-dir', required=True)
    p.add_argument('--wpy', required=True, help='Windows 파이썬 exe (/mnt/c/... 경로로 실행)')
    p.add_argument('--journal', required=True, help='저널 sqlite — 반드시 C:\\ 형식')
    a = p.parse_args()

    gates = []
    for g in filter(None, (x.strip() for x in a.gates.split(','))):
        hh, mm = g.split(':')
        gates.append((int(hh), int(mm)))
    sa = a.stop_after.split(':')
    stop_after = (int(sa[0]), int(sa[1]))

    print('watch start %s gates=%s tick=%dm stop_after=%s'
          % (dt.datetime.now().isoformat(timespec='seconds'), a.gates, a.tick, a.stop_after), flush=True)
    prev = None
    for t, kind in schedule(gates, a.tick, stop_after):
        wait = (t - dt.datetime.now()).total_seconds()
        if wait > 0:
            time.sleep(wait)
        rec = snapshot(a, kind)
        an = anomalies(rec, prev)
        if kind != 'tick' or an:
            print('[%s] %s%s' % (kind, brief(rec), (' ANOM=' + ','.join(an)) if an else ''), flush=True)
        prev = rec
    final = snapshot(a, 'final')
    print('[final] %s' % brief(final), flush=True)
    print('WATCH DONE', flush=True)


if __name__ == '__main__':
    sys.exit(main())
