"""Deterministic management views over cached Yahoo data. No upstream calls."""
import math

def number(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):
        return None

def dashboard(s, team, week):
    if not str(week).isdigit() or not 1<=int(week)<=18: raise ValueError('Select a valid week.')
    matches=s.get('teamMatchups',{})
    m=matches.get(team,s['matchups'] if team==s['team']['key'] else {}).get(str(week))
    if m is None:
        raise ValueError('Select a cached team and week.')
    roster=next((t.get('players',[]) for t in m['teams'] if t['key']==team),[])
    completed=sorted(int(w) for w,part in matches.get(team,s['matchups']).items() if part.get('status')=='postevent' and int(w)<int(week))
    recent=completed[-3:]
    alerts=[]; trends=[]; byes={}
    for p in roster:
        starter=p.get('slot') not in ('BN','IR','IR+','NA')
        if starter and str(p.get('bye'))==str(week): alerts.append(p['name']+' is on bye in the selected week.')
        if starter and p.get('status'): alerts.append(p['name']+': '+p['status']+' — verify availability on Yahoo.')
        if p.get('bye'): byes.setdefault(str(p['bye']),[]).append(p['name']+' ('+p.get('position','')+')')
        values=[number(s.get('weeklyStats',{}).get(str(w),{}).get(p['key'],{}).get('points')) for w in recent]
        earlier=[number(s.get('weeklyStats',{}).get(str(w),{}).get(p['key'],{}).get('points')) for w in completed[:-3]]
        valid=[v for v in values if v is not None]; prior=[v for v in earlier if v is not None]
        stats={}
        position=p.get('position','').upper()
        metrics={'QB':{'4','5','6','9'},'K':{'19','20','21','22','23','29','30'},'DEF':{'31','32','33','34','35'},'D/ST':{'31','32','33','34','35'}}.get(position,{'8','78','11','12'})
        for sid,label in s.get('statLabels',{}).items():
            if sid in metrics:
                parts=[number(next((x.get('value') for x in s.get('weeklyStats',{}).get(str(w),{}).get(p['key'],{}).get('stats',[]) if x['id']==sid),None)) for w in recent]
                stats[label]=sum(parts) if parts and all(v is not None for v in parts) else None
        trends.append({'key':p['key'],'name':p['name'],'position':p.get('position'),'slot':p.get('slot'),'season':number(s.get('seasonStats',{}).get(p['key'],{}).get('points')),'recent':sum(valid)/len(valid) if valid else None,'samples':len(valid),'prior':sum(prior)/len(prior) if prior else None,'volume':stats,'missingRecentWeeks':[w for w,v in zip(recent,values) if v is None]})
    for slot in s.get('rosterSlots',[]):
        position=slot['position']
        if position not in ('BN','IR','IR+','NA'):
            missing=int(slot['count'] or 0)-sum(p.get('slot')==position for p in roster)
            if missing>0: alerts.append(f'{missing} empty {position} slot(s).')
    pairs=[];seen=set()
    for weeks in matches.values():
        part=weeks.get(str(week),{}); ts=part.get('teams',[]); key=tuple(sorted(t['key'] for t in ts))
        if key and key not in seen:
            seen.add(key);pairs.append({'teams':[{'key':t['key'],'name':t['name'],'points':t.get('points'),'projected':t.get('projected')} for t in ts],'status':part.get('status')})
    return {'updated':s['updated'],'alerts':alerts,'trends':trends,'recentWeeks':recent,'byes':byes,'matchups':pairs,'standings':s.get('standings',[]),'available':s.get('availablePlayers',[]),'roster':roster,'teams':s.get('teams',[]),'allRosters':{k:next((t.get('players',[]) for t in v.get(str(week),{}).get('teams',[]) if t['key']==k),[]) for k,v in matches.items()}}
