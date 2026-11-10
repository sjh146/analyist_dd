import json
import datetime

p = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(p, encoding='utf-8'))
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
tgt = None
for it in d['items']:
    if it.get('id') == 'CG178':
        tgt = it
        break
assert tgt is not None
add = (
    " | 2026-11-10 21:2x 엔지니어 자율 — **③ 실행 불가 확인, 선행 항목 CG184 신설**: 패널 17개 전부 "
    "max date=2026-09-23(실측 scripts/_panel_periods.py) → 신선 구간(09-24~11-09)을 담은 패널이 "
    "없어 게이트 arm 재측정 자체가 성립하지 않는다. 갭 창 커버리지도 이미 완비(10-09~11-06 20/20 "
    "거래일: md·fi·smf·short)라 백필 재실행은 무의미하다. → 이 항목의 남은 ③은 CG184"
    "(150일 창·end-date 2026-11-09 고정 패널 빌드 + 같은 런 A/B) 완료 후 그 요약으로 판정한다."
)
tgt['note'] = (tgt.get('note') or '') + add
tgt['blocked_by'] = 'CG184'
tgt['updated_at'] = now
d['updated_at'] = now
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('CG178 note+blocked_by updated')
