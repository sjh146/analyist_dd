import sys
sys.path.insert(0, 'scripts')
import numpy as np
import rule_expectancy as rx

conn = rx._conn()
df = rx.add_forward(rx.load_prices(conn, '2026-01-02', '2026-11-19', None, 'STOCK'))
idx = rx.load_index(conn, '2026-01-02', '2026-11-19', 'KOSDAQ')
conn.close()

b = df[(df['value'] >= 3e8) & (df['close'] >= 1000) & df['fwd1'].notna()].merge(idx, on='dt', how='left')
cost = 0.0061
idx5 = dict(zip(idx['dt'], idx['idx_ret'].rolling(5).sum()))


def st(d, label):
    r = d['fwd1'].to_numpy(dtype=float) - cost
    if len(r) < 10:
        print('%-28s 표본 부족 n=%d' % (label, len(r)))
        return
    m = r.mean()
    t = m / (r.std(ddof=1) / np.sqrt(len(r)))
    print('%-28s n=%5d mean=%+7.3f%% t=%6.1f win=%5.1f%%' % (label, len(r), m * 100, t, 100 * (r > 0).mean()))


h1 = b['dt'] < '2026-07-01'
h2 = ~h1

# A: 시장은 버티는데 종목만 급락(개별 악재)
a = b[(b['r_day'] <= -0.08) & (b['idx_ret'] >= -0.015)]
st(a, 'A 개별급락(지수>=-1.5%)')
st(a[h1.loc[a.index]], '  A H1')
st(a[h2.loc[a.index]], '  A H2')

# B: 급락 + 지수 상승
b2 = b[(b['r_day'] <= -0.08) & (b['idx_ret'] >= 0)]
st(b2, 'B 급락+지수상승')
st(b2[h1.loc[b2.index]], '  B H1')
st(b2[h2.loc[b2.index]], '  B H2')

# C: 시장 5일 낙폭 깊음(<=-6%) + 당일 지수 반등(>=+1%)
b3 = b[b['r_day'] <= -0.08].copy()
b3['idx5'] = b3['dt'].map(idx5)
c = b3[(b3['idx5'] <= -0.06) & (b3['idx_ret'] >= 0.01)]
st(c, 'C 침체장+당일반등')
st(c[h1.loc[c.index]], '  C H1')
st(c[h2.loc[c.index]], '  C H2')

# D: 참고 — R8 전체(지수 급락일)
d8 = b[(b['r_day'] <= -0.08) & (b['idx_ret'] <= -0.015)]
st(d8, 'D R8 기준(패닉일)')
st(d8[h1.loc[d8.index]], '  D H1')
st(d8[h2.loc[d8.index]], '  D H2')
