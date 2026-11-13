#!/usr/bin/env python3
"""회선(폰 프록시 → mihomo 터널) 도달성 감시 — 이 회선은 프록시 전용이라
터널 upstream 이 없으면 egress/DNS 가 전부 죽는다(그러면 Creon·알림도 같이 죽는다).

출력 규칙(watchdog): 정상이면 침묵. 단절 시작/복구 시 1회, 단절 지속 중에는
REALERT_MIN 간격으로만 경보. 상태는 ~/.hermes/state/proxy_watch.json.
실행: python3 proxy_watch.py [--verbose]
"""
import json, os, subprocess, time, datetime, sys

TUNNEL_LOG = os.environ.get('TRADER_TUNNEL_LOG', '/mnt/c/Users/jhshi/proxy-tun/tunnel.log')
STATE = os.environ.get('TRADER_PROXY_STATE', os.path.expanduser('~/.hermes/state/proxy_watch.json'))
CURL = '/mnt/c/Windows/System32/curl.exe'
REALERT_MIN = 60


def now():
    return datetime.datetime.now()


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    json.dump(st, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def tunnel_events():
    """tunnel.log 의 마지막 'reachable / not found / mihomo started' 줄들."""
    try:
        with open(TUNNEL_LOG, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200000))
            text = f.read().decode('utf-8', 'replace')
    except Exception:
        return {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('['):
            continue
        if 'not found on any interface' in line:
            out['down'] = line
        elif 'reachable - (re)starting tunnel' in line:
            out['up'] = line
        elif 'upstream is gone' in line:
            out['gone'] = line
    return out


def probe():
    try:
        r = subprocess.run([CURL, '-s', '-o', os.devnull, '-w', '%{http_code}', '--noproxy', '*',
                            '-m', '12', 'http://www.gstatic.com/generate_204'],
                           capture_output=True, text=True, timeout=20)
        code = (r.stdout or '').strip()
    except Exception:
        code = '000'
    dns = subprocess.run(['getent', 'hosts', 'discord.com'], capture_output=True, text=True)
    mihomo = subprocess.run(['/mnt/c/Windows/System32/tasklist.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, timeout=25, errors='replace').stdout.count('mihomo.exe')
    return code, dns.returncode == 0, mihomo


def main():
    st = load()
    code, dns_ok, mihomo = probe()
    ev = tunnel_events()
    ok = (code == '204' and dns_ok)
    ts = now().isoformat(timespec='seconds')
    prev = st.get('state', 'ok')
    lines = []
    if ok:
        if prev != 'ok':
            lines.append('[회선 복구] 프록시·터널 정상 (probe 204, DNS ok, mihomo %d개)' % mihomo)
            lines.append('  직전 단절: %s' % (st.get('down_since') or '?'))
        st['state'] = 'ok'
        st['last_ok'] = ts
        st.pop('down_since', None)
        st['last_alert'] = None
    else:
        if prev == 'ok':
            st['down_since'] = ts
        st['state'] = 'down'
        last_alert = st.get('last_alert')
        due = True
        if last_alert:
            try:
                age = (now() - datetime.datetime.fromisoformat(last_alert)).total_seconds() / 60
                due = age >= REALERT_MIN
            except Exception:
                due = True
        if prev == 'ok' or due:
            st['last_alert'] = ts
            lines.append('[회선 단절] egress 불가 (probe %s, DNS %s, mihomo %d개) — Creon·알림 전송 불가' %
                         (code, 'ok' if dns_ok else 'FAIL', mihomo))
            if st.get('down_since'):
                lines.append('  지속: %s 부터' % st['down_since'])
            if ev.get('down'):
                lines.append('  tunnel: ' + ev['down'])
            if ev.get('up'):
                lines.append('  마지막 정상: ' + ev['up'])
    save(st)
    if '--verbose' in sys.argv:
        print('[proxy_watch] code=%s dns=%s mihomo=%d state=%s prev=%s' % (code, dns_ok, mihomo, st['state'], prev))
    for line in lines:
        print(line)


if __name__ == '__main__':
    main()
