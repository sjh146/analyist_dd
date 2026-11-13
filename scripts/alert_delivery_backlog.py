#!/usr/bin/env python3
"""알림 전달 실패 백로그 — 회선 복구 후 실패분을 1회 요약해 다시 보낸다.

Hermes cron(no_agent, deliver=discord)이 이 스크립트의 stdout을 그대로 전달한다.
- 이 실행 자체가 전달됐는지는 다음 실행에서 executions.db(delivery_outcome)로 확인하고,
  그때 비로소 backlog 에서 제거한다(전달 못 했는데 지우면 알림이 영영 사라진다).
- 회선이 죽어 있으면(프록시 전용 회선이라 DNS/egress 불가) 아무것도 출력하지 않고
  pending 만 쌓아둔다 — 복구되면 다음 실행이 한 번에 요약해 보낸다.
실행: python3 alerts_backlog.py --once [--verbose]
"""
import sqlite3, json, os, subprocess, datetime, sys

CRON = os.environ.get('HERMES_CRON_DIR', os.path.expanduser('~/.hermes/cron'))
DELIV = os.path.join(CRON, 'deliveries.db')
EXEC = os.path.join(CRON, 'executions.db')
JOBS = os.path.join(CRON, 'jobs.json')
STATE = os.environ.get('ALERTS_BACKLOG_STATE', os.path.expanduser('~/.hermes/state/alerts_backlog.json'))
CURL = os.environ.get('CURL_EXE', '/mnt/c/Windows/System32/curl.exe')
MAX_LINES = 12


def now():
    return datetime.datetime.now()


def ensure_dir(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save(st):
    ensure_dir(STATE)
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def job_names():
    try:
        d = json.load(open(JOBS))
        js = d if isinstance(d, list) else d.get('jobs', [])
        return {(j.get('id') or j.get('job_id')): (j.get('name') or j.get('id') or j.get('job_id'))
                for j in js}
    except Exception:
        return {}


def last_outcome(job_id):
    try:
        c = sqlite3.connect(EXEC, timeout=10)
        row = c.execute("select delivery_outcome from executions where job_id=? "
                        "order by started_at desc limit 1", (job_id,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def net_ok():
    try:
        r = subprocess.run([CURL, '-s', '-o', os.devnull, '-w', '%{http_code}', '--noproxy', '*',
                            '-m', '12', 'http://www.gstatic.com/generate_204'],
                           capture_output=True, text=True, timeout=20)
        code = (r.stdout or '').strip()
    except Exception:
        code = '000'
    dns = subprocess.run(['getent', 'hosts', 'discord.com'], capture_output=True, text=True)
    return code == '204' and dns.returncode == 0, code


def scan(since_iso, exclude=()):
    names = job_names()
    out = []
    try:
        c = sqlite3.connect(DELIV, timeout=10)
        c.execute('attach ? as ex', (EXEC,))
        rows = c.execute(
            "select d.execution_id, d.created_at, ex.job_id, coalesce(d.error,'') "
            "from deliveries d left join ex.executions ex on ex.id = d.execution_id "
            "where d.status='failed' and d.created_at >= ? "
            "order by d.created_at", (since_iso,)).fetchall()
    except Exception:
        rows = []
    for eid, ts, jid, err in rows:
        if jid and jid in exclude:
            continue
        out.append({'id': eid, 'ts': ts, 'job': names.get(jid, jid or '?'),
                    'err': classify(err)})
    return out


def classify(err):
    e = err or ''
    if 'name resolution' in e or 'DNSError' in e or 'NameResolution' in e:
        return 'DNS 실패'
    if 'TimeoutError' in e or 'timed out' in e.lower():
        return '전송 타임아웃'
    if 'ClientConnector' in e or 'Cannot connect' in e:
        return '연결 실패'
    low = e.lower()
    if not e:
        return '사유 없음'
    return e.split(':', 1)[-1].strip()[:48] if low.startswith('delivery error') else e[:48]


def render(pending):
    by = {}
    for p in pending:
        key = (p['job'], p['err'])
        by.setdefault(key, []).append(p['ts'])
    lines = []
    for (job, err), tss in sorted(by.items(), key=lambda kv: -len(kv[1])):
        tss = sorted(tss)
        span = tss[0][11:16] if len(tss) == 1 else '%s~%s' % (tss[0][11:16], tss[-1][11:16])
        lines.append('- %s %s: %s %d건' % (span, job, err, len(tss)))
    head = '[알림 복구] 회선 단절로 전달 못한 경보 %d건 (마지막 %s)' % (
        len(pending), max(p['ts'] for p in pending)[5:16].replace('T', ' '))
    if len(lines) > MAX_LINES:
        rest = sum(len(v) for k, v in list(by.items())[MAX_LINES:])
        lines = lines[:MAX_LINES] + ['- 외 %d건' % rest]
    return [head] + lines


def main():
    st = load()
    job_id = st.get('job_id')
    changed = False
    # 1) 직전 발송분이 실제로 전달됐으면 backlog 에서 제거
    if st.get('inflight') and job_id and last_outcome(job_id) == 'delivered':
        done = set(st['inflight'])
        st['pending'] = [p for p in st.get('pending', []) if p['id'] not in done]
        st['inflight'] = []
        st['last_cleared'] = now().isoformat(timespec='seconds')
        changed = True
    # 2) 새 실패 수집 (watermark 이후)
    since = st.get('watermark') or (now() - datetime.timedelta(hours=24)).isoformat(timespec='seconds')
    pending = st.setdefault('pending', [])
    have = {p['id'] for p in pending}
    exclude = set(st.get('exclude') or [])
    if job_id:
        exclude.add(job_id)  # 자기 자신의 전달 실패를 다시 알리지 않는다(무한 재귀 방지)
    for row in scan(since, exclude):
        if row['id'] not in have:
            pending.append(row)
            have.add(row['id'])
            changed = True
    st['watermark'] = now().isoformat(timespec='seconds')
    # 3) 회선 확인 — 죽어 있으면 조용히 보류
    ok, code = net_ok()
    if not ok or not pending:
        if changed:
            save(st)
        if '--verbose' in sys.argv:
            print('[backlog] pending=%d net=%s code=%s' % (len(pending), ok, code))
        return
    # 4) 요약 출력 → 전달되면 다음 실행에서 제거
    st['inflight'] = [p['id'] for p in pending]
    save(st)
    for line in render(pending):
        print(line)


if __name__ == '__main__':
    main()
