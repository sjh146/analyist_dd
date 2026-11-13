#!/usr/bin/env python3
"""개장 전 점검 (읽기 전용): 브리지·루프·세션·피드·킬스위치.

기본: 다음 08:20~08:50을 절대시각으로 기다렸다가 5분 간격 스냅샷 + 이상만 출력.
--once: 지금 1회 점검 (크론/수동용).
원자료: /home/jhshi/analyist_dd/data/reports/preopen_watch_20261113.jsonl
"""
import subprocess, json, os, sys, time, datetime

CURL = '/mnt/c/Windows/System32/curl.exe'
TADIR = '/mnt/c/Users/jhshi/analyist_dd/trader-agent'
FEED = '/home/jhshi/analyist_dd/data/feed/screener_latest.json'
HOL = '/home/jhshi/analyist_dd/data/krx_holidays.json'
OUT = '/home/jhshi/analyist_dd/data/reports/preopen_watch_20261113.jsonl'
GATES = ['08:20', '08:25', '08:30', '08:35', '08:40', '08:45', '08:50']


def run(args, t=30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=t, errors='replace')
        return r.stdout.strip()
    except Exception:
        return ''


def http(p):
    return run([CURL, '-s', '--noproxy', '*', '-m', '15', 'http://127.0.0.1:8100' + p])


def tail(path, n=4):
    try:
        return [x.strip() for x in open(path, errors='replace').read().splitlines()[-n:]]
    except Exception:
        return []


def is_trading_day(d=None):
    d = d or datetime.date.today()
    if d.weekday() >= 5:
        return False, 'weekend'
    try:
        s = json.dumps(json.load(open(HOL)), ensure_ascii=False)
        for k in (d.strftime('%Y%m%d'), d.isoformat()):
            if k in s:
                return False, 'holiday'
    except Exception:
        pass
    return True, ''


def check():
    now = datetime.datetime.now()
    rec = {'ts': now.isoformat(timespec='seconds')}
    std, why = is_trading_day()
    rec['trading_day'] = std
    rec['skip_reason'] = why
    rec['kill_switch'] = os.path.exists(os.path.join(TADIR, 'kill_switch.txt'))
    h = http('/health')
    try:
        rec['connected'] = json.loads(h).get('connected')
    except Exception:
        rec['connected'] = None
        rec['health_raw'] = h[:80]
    try:
        bj = json.loads(http('/balance')).get('balance', {})
        rec['balance_ok'] = True
        rec['equity'] = bj.get('equity')
        rec['npos'] = bj.get('positions_count')
        rec['cash'] = bj.get('cash')
    except Exception:
        rec['balance_ok'] = False
    try:
        d = json.load(open(os.path.join(TADIR, 'loop_state.json')))
        rec['halt'] = d.get('loop', {}).get('halt')
        rec['daily_date'] = d.get('daily', {}).get('date')
        rec['daily_closed'] = d.get('daily', {}).get('closed')
        rec['last_cycle_ts'] = (d.get('last_cycle') or {}).get('ts')
    except Exception:
        pass
    tc = run(['/mnt/c/Windows/System32/tasklist.exe', '/FO', 'CSV', '/NH'])
    rec['python_procs'] = tc.count('python.exe')
    rec['bridge_sup_tail'] = tail(os.path.join(TADIR, 'bridge_sup.log'), 2)
    rec['loop_sup_tail'] = tail(os.path.join(TADIR, 'loop_sup.log'), 2)
    rec['runner_tail'] = tail(os.path.join(TADIR, 'runner.log'), 3)
    try:
        f = json.load(open(FEED))
        rec['feed_generated_at'] = f.get('generated_at')
        rec['feed_counts'] = {k: len(v.get('items', []) if isinstance(v, dict) else v)
                              for k, v in (f.get('candidates') or {}).items()}
    except Exception as e:
        rec['feed_err'] = str(e)[:80]
    try:
        rec['proxy'] = proxy_state()
    except Exception as e:
        rec['proxy_err'] = str(e)[:80]
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, 'a') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
    return rec


def proxy_state():
    """회선(프록시→터널) 도달성: probe 204 + DNS + tunnel.log 마지막 이벤트."""
    try:
        r = subprocess.run([CURL, '-s', '-o', os.devnull, '-w', '%{http_code}', '--noproxy', '*',
                            '-m', '12', 'http://www.gstatic.com/generate_204'],
                           capture_output=True, text=True, timeout=20)
        code = (r.stdout or '').strip()
    except Exception:
        code = '000'
    dns = subprocess.run(['getent', 'hosts', 'discord.com'], capture_output=True, text=True)
    last = ''
    try:
        with open('/mnt/c/Users/jhshi/proxy-tun/tunnel.log', 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 120000))
            for line in f.read().decode('utf-8', 'replace').splitlines():
                if 'not found on any interface' in line or 'reachable - (re)starting tunnel' in line:
                    last = line.strip()
    except Exception:
        pass
    return {'tunnel_probe': code, 'dns_ok': dns.returncode == 0, 'tunnel_last': last}


def alarms(r, phase):
    if not r.get('trading_day'):
        return []
    a = []
    if r.get('kill_switch'):
        a.append('킬스위치 파일 존재 → 루프 정지 상태')
    px = r.get('proxy') or {}
    if px.get('tunnel_probe') != '204' or not px.get('dns_ok'):
        a.append('회선(프록시·터널) 미도달 — egress/DNS 불가 (probe %s, dns %s)'
                 % (px.get('tunnel_probe'), 'ok' if px.get('dns_ok') else 'FAIL'))
    if r.get('connected') is not True:
        a.append('브리지 미연결(/health not connected) — Creon 로그인 필요')
    if r.get('balance_ok') is False:
        a.append('/balance 조회 실패 — 세션 반쪽/끊김 의심')
    if phase in ('08:35', '08:40', '08:45', '08:50') and (r.get('python_procs') or 0) < 2:
        a.append('루프 미기동(python %s개) — 09:00 창 전 기동 필요' % r.get('python_procs'))
    if phase in ('08:40', '08:45', '08:50') and not (r.get('last_cycle_ts') or '').startswith(str(datetime.date.today())):
        a.append('오늘 사이클 기록 없음(last_cycle %s)' % r.get('last_cycle_ts'))
    return a


def brief(r):
    return ('%s %s | trading=%s conn=%s bal=%s eq=%s npos=%s halt=%s procs=%s daily=%s closed=%s feed=%s %s'
            % (r['ts'][11:19], 'once', r.get('trading_day'), r.get('connected'), r.get('balance_ok'),
               r.get('equity'), r.get('npos'), r.get('halt'), r.get('python_procs'),
               r.get('daily_date'), r.get('daily_closed'), r.get('feed_generated_at'), r.get('feed_counts')))


def wait_until(hh, mm):
    target = datetime.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target < datetime.datetime.now():
        target += datetime.timedelta(days=1)
    while datetime.datetime.now() < target:
        time.sleep(10)


if '--once' in sys.argv:
    r = check()
    a = alarms(r, 'once')
    quiet_ok = '--verbose' not in sys.argv
    if a:
        print('[PREOPEN ALERT] ' + brief(r))
        for x in a:
            print('  - ' + x)
        print('  loop_sup: ' + ' / '.join(r.get('loop_sup_tail') or []))
        print('  bridge_sup: ' + ' / '.join(r.get('bridge_sup_tail') or []))
    elif not quiet_ok:
        print('[PREOPEN OK] ' + brief(r))
    sys.exit(0)

print('preopen watch armed: gates %s (now %s)' % (', '.join(GATES), datetime.datetime.now().isoformat(timespec='seconds')), flush=True)
seen = set()
for g in GATES:
    hh, mm = [int(x) for x in g.split(':')]
    wait_until(hh, mm)
    r = check()
    a = alarms(r, g)
    print('[%s] %s' % (g, brief(r)), flush=True)
    for x in a:
        print('[%s][ALERT] %s' % (g, x), flush=True)
    if a:
        print('  loop_sup: ' + ' / '.join(r.get('loop_sup_tail') or []), flush=True)
        print('  bridge_sup: ' + ' / '.join(r.get('bridge_sup_tail') or []), flush=True)
print('PREOPEN DONE', flush=True)
