"""The only process allowed to call Yahoo. Publish atomically once per UTC day."""
import copy
import json
import os
from pathlib import Path
import time
import server

DATA = Path(os.getenv('DATA_DIR', '/app/data'))
PUBLIC = Path(os.getenv('SNAPSHOT_DIR', '/snapshots'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.chmod(0o600 if path.name=='private-snapshot.json' else 0o644)
    temp.replace(path)



def backfill_weekly(y, league_key, snapshot, keys):
    """Fill roster-history gaps in batches, only for completed league weeks."""
    completed={w for weeks in snapshot['teamMatchups'].values() for w,m in weeks.items() if m.get('status')=='postevent'}
    for week in sorted(completed,key=int):
        rows=snapshot['weeklyStats'].setdefault(week,{})
        missing=[k for k in keys if k not in rows or rows[k].get('points') in (None,'') or not rows[k].get('stats')]
        for start in range(0,len(missing),25):
            batch=missing[start:start+25]
            root=y.get(f"league/{league_key}/players;player_keys={','.join(batch)}/stats;type=week;week={week}")
            for n in root.findall('.//player'):
                p=y.player(n)
                if p['key'] in batch:
                    rows[p['key']]={'points':p['points'],'stats':p['stats']}


def refresh():
    y = server.yahoo
    league_key, team_key = os.getenv('PUBLIC_LEAGUE_KEY'), os.getenv('PUBLIC_TEAM_KEY')
    if not league_key or not team_key:
        raise ValueError('Set PUBLIC_LEAGUE_KEY and PUBLIC_TEAM_KEY before publication.')
    league = next((l for l in y.leagues() if l['key']==league_key), None)
    if not league:
        raise ValueError('Configured league is not accessible.')
    teams = y.teams(league_key)
    if not any(t['key']==team_key and t['mine'] for t in teams):
        raise ValueError('Configured team must be owned by your Yahoo account.')
    snapshot = {'schemaVersion':6, 'league':league, 'team':next(t for t in teams if t['key']==team_key),
                'teams':copy.deepcopy(teams),
                'teamMatchups':{t['key']:{} for t in teams},
                'matchups':{}, 'seasonStats':{}, 'weeklyStats':{}, 'updated':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    keys = set()
    for week in range(int(league['startWeek']), int(league['endWeek'])+1):
        snapshot['weeklyStats'][str(week)] = {}
        for selected in teams:
            m = y.matchup(league_key, selected['key'], str(week))
            for fantasy_team in m['teams']:
                fantasy_team['manager']=next((t.get('manager','') for t in teams if t['key']==fantasy_team['key']),'')
            snapshot['teamMatchups'][selected['key']][str(week)] = m
            if selected['key']==team_key:
                snapshot['matchups'][str(week)] = m
                snapshot['statLabels'] = m['statLabels']
            for t in m['teams']:
                for p in t['players']:
                    keys.add(p['key'])
                    snapshot['weeklyStats'][str(week)][p['key']] = {'points':p['points'],'stats':p['stats']}
    keys = sorted(keys)
    backfill_weekly(y,league_key,snapshot,keys)
    for start in range(0,len(keys),25):
        root = y.get(f"league/{league_key}/players;player_keys={','.join(keys[start:start+25])}/stats;type=season")
        for n in root.findall('.//player'):
            p = y.player(n)
            snapshot['seasonStats'][p['key']] = {'points':p['points'],'stats':p['stats']}
    settings=y.get(f'league/{league_key}/settings')
    snapshot['rosterSlots']=[{'position':server.value(n,'position'),'count':server.value(n,'count')} for n in settings.findall('.//roster_positions/roster_position')]
    root=y.get(f'league/{league_key}/standings')
    snapshot['standings']=[{'key':server.value(n,'team_key'),'name':server.value(n,'name'),'rank':server.value(n,'team_standings/rank'),'wins':server.value(n,'team_standings/outcome_totals/wins'),'losses':server.value(n,'team_standings/outcome_totals/losses'),'ties':server.value(n,'team_standings/outcome_totals/ties'),'for':server.value(n,'team_standings/points_for',server.value(n,'team_points/total')),'against':server.value(n,'team_standings/points_against',server.value(n,'team_points_against/total'))} for n in root.findall('.//team')]
    root=y.get(f'league/{league_key}/players;status=A;sort=PTS;sort_type=season;count=25/stats;type=season')
    snapshot['availablePlayers']=[y.player(n) for n in root.findall('.//player')]
    import schedule
    try:
        nfl_schedule=schedule.fetch_schedule(league.get('season',''))
        schedule_updated=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    except Exception:
        # Schedule outages must not block Yahoo refreshes. Reuse only the same season.
        try:
            old=json.loads((DATA/'private-snapshot.json').read_text())
        except (OSError,ValueError): old={}
        same=old.get('league',{}).get('season')==league.get('season')
        nfl_schedule=old.get('nflSchedule',{}) if same else {}
        schedule_updated=old.get('scheduleUpdated') if same else None
    snapshot['nflSchedule']=nfl_schedule
    snapshot['scheduleUpdated']=schedule_updated
    schedule.enrich(snapshot,nfl_schedule)
    publish(snapshot)


def publish(snapshot):
    team_key=snapshot['team']['key']
    write(DATA/'private-snapshot.json',snapshot)
    public = copy.deepcopy(snapshot)
    del public['standings']
    del public['availablePlayers']
    del public['teams']
    del public['teamMatchups']
    public_keys = {p['key'] for m in public['matchups'].values() for t in m['teams'] for p in t['players']}
    for week,rows in public['weeklyStats'].items():
        public['weeklyStats'][week] = {k:v for k,v in rows.items() if k in public_keys}
    public['seasonStats'] = {k:v for k,v in public['seasonStats'].items() if k in public_keys}
    public['league']['name'] = 'Fantasy Football'
    public['team'] = {'key':team_key,'name':'Featured team','mine':True}
    for m in public['matchups'].values():
        for t in m['teams']:
            t.pop('manager',None)
            t['name'] = 'Featured team' if t['mine'] else 'Opponent'
    write(PUBLIC/'public.json', public)


def full_refresh_due(last_full, hours, now):
    return now-last_full>=hours*3600


def active_weeks(snapshot, now):
    from datetime import datetime
    weeks=set()
    for week,teams in snapshot.get('nflSchedule',{}).items():
        for game in teams.values():
            try: kickoff=datetime.fromisoformat(game['kickoff']).timestamp()
            except (KeyError,TypeError,ValueError): continue
            if kickoff-900<=now<=kickoff+21600: weeks.add(week)
    return sorted(weeks,key=int)


def refresh_scores(snapshot, weeks):
    """One scoreboard plus one roster request per fantasy team/week."""
    y=server.yahoo;league=snapshot['league']['key']
    import schedule
    for week in weeks:
        board=y.get(f'league/{league}/scoreboard;week={week}')
        boards={server.value(t,'team_key'):t for t in board.findall('.//matchup/teams/team')}
        statuses={server.value(t,'team_key'):server.value(m,'status') for m in board.findall('.//matchup') for t in m.findall('teams/team')}
        rosters={key:y.roster(key,week) for key in boards if key in snapshot.get('teamMatchups',{})}
        for matches in [snapshot['matchups'],*snapshot.get('teamMatchups',{}).values()]:
            m=matches.get(week)
            if not m: continue
            for t in m['teams']:
                key=t['key'];n=boards.get(key)
                if n is None: continue
                t['points']=server.value(n,'team_points/total',None)
                t['projected']=server.value(n,'team_projected_points/total',None)
                if key in rosters: t['players']=copy.deepcopy(rosters[key])
                m['status']=statuses[key]
        for players in rosters.values():
            for p in players:
                snapshot['weeklyStats'].setdefault(week,{})[p['key']]={'points':p['points'],'stats':p['stats']}
    schedule.enrich(snapshot,snapshot.get('nflSchedule',{}))
    snapshot['scoresUpdated']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    publish(snapshot)


def live_tick(accounts):
    state=accounts.live_state()
    try: snapshot=json.loads((DATA/'private-snapshot.json').read_text())
    except (OSError,ValueError): return
    weeks=active_weeks(snapshot,time.time())
    if state['enabled'] and weeks:
        # Reserve a conservative budget including one OAuth retry per request.
        estimate=2*len(weeks)*(1+len(snapshot.get('teams',[])))
        state=accounts.live_state(reserve_calls=estimate)
        if state['reserved']:
            try: refresh_scores(snapshot,weeks)
            except Exception:
                accounts.live_finished(False)
            else: accounts.live_finished(True)
            state=accounts.live_state()
    status={k:state.get(k) for k in ('enabled','nextPoll','lastSuccess','lastResult','calls')}
    status.update(active=bool(weeks),intervalSeconds=300,dailyCallBudget=2000)
    write(PUBLIC/'live.json',status)
    write(DATA/'live.json',status)


if __name__=='__main__':
    import sys
    if '--once' in sys.argv:
        refresh()
    else:
        from accounts import Accounts
        accounts=Accounts(DATA/'accounts.sqlite3')
        next_daily_attempt=0
        while True:
            day=time.strftime('%Y-%m-%d',time.gmtime())
            try:
                saved=json.loads((DATA/'private-snapshot.json').read_text())
                upgraded=saved.get('schemaVersion',0)>=6
                from datetime import datetime
                last_full=datetime.fromisoformat(saved['updated'].replace('Z','+00:00')).timestamp()
            except (OSError,ValueError):
                upgraded=False
                last_full=0
            job=accounts.claim_refresh()
            daily=(not upgraded or full_refresh_due(last_full,accounts.refresh_interval(),time.time())) and time.time()>=next_daily_attempt
            if job or daily:
                try:
                    refresh()
                    if job: accounts.finish_refresh(job,True)
                    print('Yahoo snapshot published.',flush=True)
                except Exception as e:
                    if job: accounts.finish_refresh(job,False)
                    print('Snapshot failed; retaining previous snapshot. '+type(e).__name__,flush=True)
                next_daily_attempt=time.time()+3600
            try:
                live_tick(accounts)
            except Exception as e:
                print('Live scoring worker check failed: '+type(e).__name__,flush=True)
            time.sleep(30)
