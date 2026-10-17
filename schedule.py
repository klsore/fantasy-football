"""NFL schedule enrichment, fetched only by the backend refresh worker."""
import csv
from datetime import datetime, timezone
import io
import urllib.request
from zoneinfo import ZoneInfo

SOURCE='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'

def team_code(code):
    code=(code or '').upper()
    return {'JAC':'JAX','WSH':'WAS','LAR':'LA'}.get(code,code)

def parse_schedule(text, season):
    weeks={}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get('season')!=str(season) or row.get('game_type')!='REG': continue
        week=row.get('week','')
        if not week.isdigit() or not 1<=int(week)<=18: continue
        kickoff=None
        try:
            kickoff=datetime.strptime(row['gameday']+' '+row['gametime'],'%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc).isoformat()
        except (KeyError,ValueError): pass
        home,away=team_code(row.get('home_team')),team_code(row.get('away_team'))
        for own,opp,location in ((home,away,'home'),(away,home,'away')):
            weeks.setdefault(week,{})[own]={'opponent':opp,'location':'neutral' if row.get('location')=='Neutral' else location,'kickoff':kickoff,'date':row.get('gameday')}
    return weeks

def fetch_schedule(season):
    with urllib.request.urlopen(SOURCE,timeout=25) as response:
        text=response.read(8_000_001)
    if len(text)>8_000_000: raise ValueError('Schedule response too large.')
    return parse_schedule(text.decode('utf-8-sig'),season)

def enrich(snapshot, schedule):
    for weeks in [snapshot['matchups'],*snapshot.get('teamMatchups',{}).values()]:
        for week,match in weeks.items():
            for team in match['teams']:
                for player in team['players']:
                    player['nflGame']={'bye':True} if str(player.get('bye'))==str(week) else schedule.get(str(week),{}).get(team_code(player.get('team')))
