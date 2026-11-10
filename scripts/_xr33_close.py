import json

p = 'docs/QUANT_MODEL_BACKLOG.json'
raw = open(p, encoding='utf-8').read()
d = json.loads(raw)
items = d['items'] if isinstance(d, dict) and 'items' in d else d

if isinstance(items, dict):
    seq = list(items.values())
else:
    seq = items

tgt = None
for it in seq:
    if it.get('id') == 'XR33':
        tgt = it
        break
assert tgt is not None, 'XR33 not found'

note_add = (
    " | 2026-11-10 21:0x 엔지니어 자율 — **셋업 선행조건 충족 확인(백필 재실행 불필요)**. "
    "DB 실측(읽기전용, `python3 scripts/_xr33_backfill_check.py` → verdict=complete): "
    "공백창 2026-10-12~10-31(평일 15거래일) 기준 market_data 15/15일(max 2026-11-10) · "
    "foreign_institutional 15/15일·14,081행·foreign_net_buy_qty 비영 98.3% · "
    "supply_market_features 15/15일(max 2026-11-09). 즉 yfinance 가격 스윕 + 수급 백필이 "
    "이미 공백을 메웠고 60일 창 1회 백필은 돌릴 필요가 없다 → 데이터 전제는 해소. "
    "남은 실제 레버는 '복구된 창이 패널에 들어갔는가'이며, 현행 스윕 패널(panel_420_asofpatch, "
    "281거래일)은 2026-09 스냅샷이라 복구분이 미반영이다 → 패널 재빌드(대략 1000분대)는 "
    "저녁 파이프라인 종료 + 런처 창(20:35~21:00)에서만 착수 가능하므로 다음 틱/다음 밤으로 이월."
)

tgt['status'] = 'done'
tgt['success'] = ('공백창(2026-10-12~10-31, 15거래일)의 market_data·foreign_institutional·'
                  'supply_market_features 가 15/15 거래일 채워짐 — 실측 verdict=complete')
tgt['verify_cmd'] = 'python3 scripts/_xr33_backfill_check.py'
tgt['note'] = (tgt.get('note') or '') + note_add
tgt['closed_at'] = '2026-11-10T21:05:00+09:00'
tgt['closed_by'] = 'engineer'

json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('XR33 ->', tgt['status'])
from collections import Counter
print(Counter(i.get('status') for i in seq))
