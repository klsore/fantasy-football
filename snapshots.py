"""Public-safe snapshots. This module never imports Yahoo or Azure clients."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def load(path):
    if not Path(path).exists():
        raise ValueError('The daily snapshot is not available yet. Please check back later.')
    return json.loads(Path(path).read_text(encoding='utf-8'))


def player_stats(snapshot, player, mode, start, end):
    labels = snapshot['statLabels']
    if player not in snapshot['seasonStats']:
        raise ValueError('Player is not in the published snapshot.')
    if mode == 'season':
        p = snapshot['seasonStats'][player]
        return dict(period='Season to date', mode=mode, points=p['points'], stats=p['stats'], statLabels=labels, allZero=False, omitted=[])
    if start > end:
        raise ValueError('Choose an end week on or after the start week.')
    totals, points, missing, omitted = {}, Decimal(0), set(), set()
    points_ok = True
    import re
    for week in range(start, end+1):
        p = snapshot['weeklyStats'].get(str(week), {}).get(player)
        if p is None:
            raise ValueError('That player/week is not in the daily snapshot. No live request was made.')
        try:
            points += Decimal(p['points'])
        except (InvalidOperation, TypeError):
            points_ok = False
        for stat in p['stats']:
            sid = stat['id']
            if mode == 'range' and re.search(r'%|avg|average|rate|per |pct|rating', labels.get(sid, ''), re.I):
                omitted.add(labels.get(sid, sid))
                continue
            totals.setdefault(sid, Decimal(0))
            try:
                n = Decimal(stat['value'])
                if not n.is_finite():
                    raise InvalidOperation
                totals[sid] += n
            except (InvalidOperation, TypeError):
                missing.add(sid)
    return dict(period=f'Week {start}' if mode=='week' else f'Weeks {start}-{end}', mode=mode,
                points=str(points) if points_ok else None,
                stats=[{'id':k,'value':str(v) if k not in missing else None} for k,v in totals.items()],
                statLabels=labels, allZero=bool(totals) and not missing and all(v==0 for v in totals.values()), omitted=sorted(omitted))
