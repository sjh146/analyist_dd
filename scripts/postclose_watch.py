#!/usr/bin/env python3
"""장 마감 관문 감시: 15:10 postclose 틱 / 15:35 순서 틱 / 루프 close 사이클(≈15:40) 결과.

원자료: /home/jhshi/analyist_dd/data/reports/postclose_watch_20261113.jsonl
stdout: 관문별 요약 + 이상 징후.
"""
import subprocess, json, os, time, datetime, sqlite3

from _paths import proj

CURL = os.environ.get('CURL_EXE', '/mnt/c/Windows/System32/curl.exe')
WPY = os.environ.get('TRADER_PY64', '/mnt/c/Users/jhshi/Python312-64/python.exe')
JW = os.environ.get('JW_DUMP', r'C:\Users\jhshi\Temp\jw_dump.py')
TADIR = os.environ.get('TRADER_AGENT_DIR', '/mnt/c/Users/jhshi/analyist_dd/trader-agent')
EXEC = os.path.expanduser('~/.hermes/cron/executions.db')
JOBS = os.path.expanduser('~/.hermes/cron/jobs.json')
OUT = proj('data/reports/postclose_watch_%s.jsonl' % datetime.date.today().strftime('%Y%m%d'))

WATCH_JOBS = {
    '5773c10b7147': 'quant-trader-postclose(15:10)',
    '43e18dca7279': 'quant-postclose-ticks(15:35)',
    '9cd4475f2be9': '감사·장마감 후(15:50)',
    'f7770e343c95': '계획락·큐심사(15:55)',
}
GATES = ['15:08', '15:13', '15:37', '15:43', '15:46']


def run(args, t=45):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=t, errors='replace')
        return r.stdout.strip()
    except Exception:
        return ''


def http(p):
    return run([CURL, '-s', '--noproxy', '*', '-m', '20', 'http://127.0.0.1:8100' + p])


def cron_today():
    out = {}
    try:
        c = sqlite3.connect(EXEC, timeout=10)
        for jid, name in WATCH_JOBS.items():
            row = c.execute(
                "select status,started_at,finished_at,delivery_outcome,substr(coalesce(error,''),1,90) "
                "from executions where job_id=? and started_at >= ? order by started_at desc limit 1",
                (jid, datetime.date.today().isoformat() + 'T15:0')).fetchone()
            out[name] = row
    except Exception as e:
        out['err'] = str(e)[:100]
    return out


def snap(kind):
    rec = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'kind': kind}
    try:
        rec['connected'] = json.loads(http('/health')).get('connected')
    except Exception:
        rec['connected'] = None
    try:
        bj = json.loads(http('/balance')).get('balance', {})
        rec['equity'] = bj.get('equity'); rec['cash'] = bj.get('cash'); rec['npos'] = bj.get('positions_count')
    except Exception:
        pass
    try:
        rec['positions'] = [{k: x.get(k) for k in ('code', 'name', 'qty', 'avg_price', 'eval_amount', 'eval_pnl')}
                            for x in json.loads(http('/positions')).get('positions', [])]
    except Exception:
        rec['positions'] = None
    try:
        d = json.load(open(os.path.join(TADIR, 'loop_state.json')))
        rec['halt'] = d.get('loop', {}).get('halt')
        rec['halt_reason'] = d.get('loop', {}).get('halt_reason')
        rec['daily'] = d.get('daily', {})
        rec['last_cycle'] = {k: (d.get('last_cycle') or {}).get(k) for k in ('ts', 'phase', 'exits', 'acted')}
    except Exception as e:
        rec['loop_state_err'] = str(e)[:80]
    try:
        lines = open(os.path.join(TADIR, 'runner.log'), errors='replace').read().splitlines()
        marker = 'cycle: ts=' + datetime.date.today().isoformat()
        idx = max((i for i, l in enumerate(lines) if marker in l), default=-1)
        seg = lines[idx:] if idx >= 0 else lines[-40:]
        rec['runner_close'] = [x.strip() for x in seg if 'CLOSE' in x or 'NOTIFY market close' in x][-6:]
        rec['runner_tail'] = [x.strip() for x in lines[-3:]]
    except Exception:
        pass
    try:
        rec['cron'] = cron_today()
    except Exception:
        pass
    j = run([WPY, JW], 60)
    try:
        rec['journal'] = json.loads(j)
    except Exception:
        rec['journal_raw'] = j[:160]
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, 'a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
    return rec


def brief(r):
    j = r.get('journal') or {}
    d = r.get('daily') or {}
    pos = r.get('positions') or []
    closed = [(x['code'], x['name'], x['qty'], x['exit_price'], x['pnl'], x['reason'])
              for x in (j.get('closed_today') or [])]
    return json.dumps({
        'ts': r['ts'][11:19], 'kind': r['kind'], 'conn': r.get('connected'), 'equity': r.get('equity'),
        'npos': r.get('npos'), 'halt': r.get('halt'),
        'daily': {'entries': d.get('entries'), 'closed': d.get('closed'), 'pnl': d.get('closed_pnl')},
        'last_cycle': r.get('last_cycle'),
        'pos': [(x['code'], x['qty'], x['eval_pnl']) for x in pos],
        'closed_today': closed,
        'runner_close': r.get('runner_close', [])[-3:],
        'cron': r.get('cron'),
    }, ensure_ascii=False)


def wait_until(hh, mm):
    t = datetime.datetime.now().replace(hour=hh, minute=mm, second=5, microsecond=0)
    while datetime.datetime.now() < t:
        time.sleep(10)


if __name__ == '__main__':
    print('postclose watch start %s' % datetime.datetime.now().isoformat(timespec='seconds'), flush=True)
    prev = None
    for g in GATES:
        hh, mm = [int(x) for x in g.split(':')]
        wait_until(hh, mm)
        r = snap('gate_' + g.replace(':', ''))
        print('[%s] %s' % (g, brief(r)), flush=True)
        a = []
        if r.get('connected') is not True:
            a.append('브리지 미연결')
        if r.get('halt'):
            a.append('halt=true (%s)' % (r.get('halt_reason') or ''))
        for name, row in (r.get('cron') or {}).items():
            if isinstance(row, tuple) and row and row[0] not in (None, 'completed'):
                a.append('%s 상태=%s' % (name, row[0]))
        if g == '15:43' and not (r.get('daily') or {}).get('closed'):
            a.append('루프 close 사이클 미완료(daily.closed=false)')
        for x in a:
            print('[%s][ALERT] %s' % (g, x), flush=True)
        prev = r
    print('POSTCLOSE WATCH DONE', flush=True)
