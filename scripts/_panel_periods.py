import glob
import numpy as np

for f in sorted(glob.glob('/app/app/models/wf/panel_*.npz')):
    try:
        z = np.load(f, allow_pickle=True)
        if 'dates' not in z:
            print(f.split('/')[-1], 'keys', list(z.keys())[:6])
            continue
        d = sorted(set(str(x) for x in z['dates']))
        rows = z['X'].shape[0] if 'X' in z else None
        print('%-32s rows=%-7s dates %s -> %s (n=%d)' % (f.split('/')[-1], rows, d[0], d[-1], len(d)))
    except Exception as e:
        print(f, 'ERR', repr(e)[:120])
