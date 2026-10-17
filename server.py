"""Personal, read-only Yahoo Fantasy dashboard. Python standard library only."""
import base64
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from intelligence import Intelligence, IntelligenceError

ROOT = Path(__file__).parent
if (ROOT / '.env').exists():
    for line in (ROOT / '.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


class APIError(Exception):
    pass


def value(node, path, default=''):
    return node.findtext(path, default) if node is not None else default


def xml(raw):
    root = ET.fromstring(raw)
    for node in root.iter():
        node.tag = node.tag.split('}')[-1]
    return root


class Yahoo:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.expires = 0
        self.cache = {}
        self.token_file = ROOT / 'data' / 'refresh-token.json'

    def access_token(self):
        with self.lock:
            if self.token and time.time() < self.expires:
                return self.token
            client = os.getenv('YAHOO_CLIENT_ID')
            secret = os.getenv('YAHOO_CLIENT_SECRET')
            refresh = os.getenv('YAHOO_REFRESH_TOKEN')
            if self.token_file.exists():
                saved = json.loads(self.token_file.read_text())
                # A newly configured token takes precedence over a previous session.
                if saved.get('original') == refresh:
                    refresh = saved['refresh_token']
            if not all([client, secret, refresh]):
                raise APIError('Add your Yahoo credentials to .env, then restart the app.')
            body = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': refresh}).encode()
            auth = base64.b64encode(f'{client}:{secret}'.encode()).decode()
            req = urllib.request.Request('https://api.login.yahoo.com/oauth2/get_token', body,
                                         {'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'})
            try:
                with urllib.request.urlopen(req, timeout=25) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as e:
                raise APIError(f'Yahoo authentication failed (HTTP {e.code}). Check API approval and refresh token.') from None
            self.token = result['access_token']
            self.expires = time.time() + int(result.get('expires_in', 3600)) - 90
            if result.get('refresh_token'):
                self.token_file.parent.mkdir(exist_ok=True)
                temp = self.token_file.with_suffix('.tmp')
                temp.write_text(json.dumps({'original': os.getenv('YAHOO_REFRESH_TOKEN'), 'refresh_token': result['refresh_token']}))
                temp.chmod(0o600)
                temp.replace(self.token_file)
            return self.token

    def get(self, path):
        cached = self.cache.get(path)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        for attempt in range(2):
            req = urllib.request.Request('https://fantasysports.yahooapis.com/fantasy/v2/' + path,
                                         headers={'Authorization': 'Bearer ' + self.access_token()})
            try:
                with urllib.request.urlopen(req, timeout=25) as response:
                    result = xml(response.read())
                self.cache[path] = (time.time(), result)
                return result
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.expires = 0
                    continue
                raise APIError(f'Yahoo returned HTTP {e.code}. Check league access and Yahoo Fantasy API approval; try again shortly.') from None

    def leagues(self):
        root = self.get('users;use_login=1/games;game_keys=nfl/leagues')
        return [{'key': value(n, 'league_key'), 'name': value(n, 'name'),
                 'season': value(n, 'season'), 'week': value(n, 'current_week', '1'),
                 'startWeek': value(n, 'start_week', '1'), 'endWeek': value(n, 'end_week', '18')}
                for n in root.findall('.//league')]

    def teams(self, league):
        root = self.get(f'league/{league}/teams')
        return [{'key': value(n, 'team_key'), 'name': value(n, 'name'),
                 'mine': value(n, 'is_owned_by_current_login') == '1',
                 'manager': value(n, 'managers/manager/nickname')}
                for n in root.findall('.//team')]

    def roster(self, team, week):
        root = self.get(f'team/{team}/roster;week={week}/players/stats;type=week;week={week}')
        return [self.player(n) for n in root.findall('.//player')]

    def player_stats(self, league, player, mode, start=None, end=None):
        if player.split('.')[0] != league.split('.')[0]:
            raise APIError('Player and league must belong to the same season.')
        settings = self.get(f'league/{league}/settings')
        labels = {value(s, 'stat_id'): value(s, 'display_name', value(s, 'name'))
                  for s in settings.findall('.//stat_categories/stats/stat')}
        base = f'league/{league}/players;player_keys={player}/stats'

        def fetch(suffix):
            node = self.get(base + suffix).find('.//player')
            if node is None:
                raise APIError('Yahoo has no statistics available for this player.')
            return self.player(node)

        omitted = []
        if mode == 'season':
            result = fetch(';type=season')
            period = 'Season to date'
        elif mode == 'week':
            result = fetch(f';type=week;week={start}')
            period = f'Week {start}'
        else:
            if start > end:
                raise APIError('Start week must be before or equal to end week.')
            totals = {}
            points = Decimal(0)
            points_available = True
            missing = set()
            for week in range(start, end + 1):
                part = fetch(f';type=week;week={week}')
                try:
                    points += Decimal(part['points'])
                except (InvalidOperation, TypeError):
                    points_available = False
                for stat in part['stats']:
                    sid = stat['id']
                    # Rates cannot be summed meaningfully across weeks.
                    if re.search(r'%|avg|average|rate|per |pct|rating', labels.get(sid, ''), re.I):
                        omitted.append(labels.get(sid, sid))
                        continue
                    totals.setdefault(sid, Decimal(0))
                    try:
                        amount = Decimal(stat['value'])
                        if not amount.is_finite():
                            raise InvalidOperation
                        totals[sid] = totals.get(sid, Decimal(0)) + amount
                    except (InvalidOperation, TypeError):
                        missing.add(sid)
            result = {'points': str(points) if points_available else None,
                      'stats': [{'id': sid, 'value': str(total) if sid not in missing else None}
                                for sid, total in totals.items()]}
            period = f'Weeks {start}–{end}'
        stats = result['stats']
        numeric = []
        for s in stats:
            try:
                numeric.append(Decimal(s['value']))
            except (InvalidOperation, TypeError):
                pass
        return {'period': period, 'mode': mode, 'points': result['points'], 'stats': stats,
                'statLabels': labels, 'allZero': bool(numeric) and all(n == 0 for n in numeric),
                'omitted': sorted(set(omitted))}

    @staticmethod
    def player(n):
        return {'key': value(n, 'player_key'), 'name': value(n, 'name/full'),
                'position': value(n, 'display_position'), 'slot': value(n, 'selected_position/position'),
                'team': value(n, 'editorial_team_abbr'), 'status': value(n, 'status'),
                'statusFull': value(n, 'status_full'), 'injuryNote': value(n, 'injury_note'),
                'eligiblePositions': [s.text for s in n.findall('eligible_positions/position') if s.text],
                'photo': value(n, 'headshot/url'), 'points': value(n, 'player_points/total', None),
                'bye': value(n, 'bye_weeks/week'),
                'stats': [{'id': value(s, 'stat_id'), 'value': value(s, 'value')} for s in n.findall('player_stats/stats/stat')]}

    def matchup(self, league, team, week):
        allowed = self.teams(league)
        if team not in [t['key'] for t in allowed]:
            raise APIError('Select a team in this league.')
        board = self.get(f'league/{league}/scoreboard;week={week}')
        found = None
        for match in board.findall('.//matchup'):
            if any(value(t, 'team_key') == team for t in match.findall('teams/team')):
                found = match
                break
        settings = self.get(f'league/{league}/settings')
        labels = {value(s, 'stat_id'): value(s, 'display_name', value(s, 'name')) for s in settings.findall('.//stat_categories/stats/stat')}
        if found is None:
            return {'week': week, 'status': 'No scheduled matchup / bye week', 'teams': [], 'statLabels': labels}
        teams = []
        for n in found.findall('teams/team'):
            key = value(n, 'team_key')
            teams.append({'key': key, 'name': value(n, 'name'), 'mine': key == team,
                          'points': value(n, 'team_points/total', None),
                          'projected': value(n, 'team_projected_points/total', None),
                          'players': self.roster(key, week)})
        teams.sort(key=lambda t: not t['mine'])
        return {'week': week, 'status': value(found, 'status'), 'teams': teams, 'statLabels': labels,
                'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


yahoo = Yahoo()


def validated(query, name, pattern):
    text = query.get(name, [''])[0]
    if not re.fullmatch(pattern, text):
        raise APIError(f'Invalid {name}.')
    return text


intelligence = Intelligence(ROOT)


def analysis_payload(match, league):
    teams = []
    for team in match['teams']:
        keys = ','.join(p['key'] for p in team['players'])
        season = {}
        if keys:
            root = yahoo.get(f'league/{league}/players;player_keys={keys}/stats;type=season')
            season = {value(n, 'player_key'): yahoo.player(n) for n in root.findall('.//player')}
        players = []
        for p in team['players']:
            total = season.get(p['key'], {})
            players.append({'name': p['name'], 'position': p['position'], 'slot': p['slot'],
                            'status': p['status'], 'statusFull': p.get('statusFull'),
                            'injuryNote': p.get('injuryNote'), 'byeWeek': p.get('bye'),
                            'eligiblePositions': p.get('eligiblePositions', []), 'weeklyPoints': p['points'],
                            'seasonPoints': total.get('points'), 'seasonStats': total.get('stats', [])})
        teams.append({'side': 'your team' if team['mine'] else 'opponent',
                      'weeklyPoints': team['points'], 'projectedPoints': team['projected'], 'players': players})
    return {'week': match['week'], 'status': match['status'], 'statLabels': match['statLabels'], 'teams': teams,
            'retrievedAtUTC': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'freshness': 'Yahoo responses may be cached up to 60 seconds. Injury designations are current available metadata, not historical injury snapshots or a live news feed.'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Avoid logging query strings and personal data.

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['matchup_ai'].value if 'matchup_ai' in cookie else ''
        except Exception:
            return ''

    def send(self, status, data, content_type='application/json', cookie=None):
        raw = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' https:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == '/api/health':
                return self.send(200, {'ok': True})
            if parsed.path == '/api/config':
                return self.send(200, {'aiEnabled': False})
            if parsed.path == '/api/ai/session':
                return self.send(200, intelligence.status(self.session()))
            if parsed.path == '/api/leagues':
                return self.send(200, yahoo.leagues())
            if parsed.path == '/api/player-stats':
                league = validated(q, 'league', r'\d+\.l\.\d+')
                player = validated(q, 'player', r'\d+\.p\.\d+')
                mode = validated(q, 'mode', r'(?:season|week|range)')
                start = end = None
                if mode != 'season':
                    start = int(validated(q, 'start', r'(?:[1-9]|1[0-8])'))
                    end = int(validated(q, 'end', r'(?:[1-9]|1[0-8])')) if mode == 'range' else start
                return self.send(200, yahoo.player_stats(league, player, mode, start, end))
            if parsed.path in ('/api/teams', '/api/matchup'):
                league = validated(q, 'league', r'\d+\.l\.\d+')
                if parsed.path == '/api/teams':
                    return self.send(200, yahoo.teams(league))
                team = validated(q, 'team', r'\d+\.l\.\d+\.t\.\d+')
                week = validated(q, 'week', r'(?:[1-9]|1[0-8])')
                return self.send(200, yahoo.matchup(league, team, week))
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript'), '/report.js': ('report.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if parsed.path in files:
                name, mime = files[parsed.path]
                return self.send(200, (ROOT / 'public' / name).read_bytes(), mime)
            self.send(404, {'error': 'Not found'})
        except IntelligenceError as e:
            self.send(e.status, {'error': str(e)})
        except APIError as e:
            self.send(400, {'error': str(e)})
        except Exception:
            self.send(502, {'error': 'Unable to load data. Check your connection and try again.'})

    def do_POST(self):
        # Same-origin JSON requests only; no arbitrary prompts or API proxy.
        if self.path not in ('/api/analytics', '/api/ai/unlock', '/api/ai/lock'):
            return self.send(404, {'error': 'Not found'})
        if self.headers.get('Origin') and urllib.parse.urlparse(self.headers['Origin']).netloc != self.headers.get('Host'):
            return self.send(403, {'error': 'Invalid origin'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'JSON requests required'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size < 2048:
                raise APIError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/ai/unlock':
                password = body.get('password', '')
                if not isinstance(password, str):
                    raise IntelligenceError('Invalid password.', 400)
                token = intelligence.login(password, self.client_address[0])
                secure = '; Secure' if os.getenv('AI_SECURE_COOKIE', '').lower() == 'true' else ''
                return self.send(200, {'ok': True}, cookie=f'matchup_ai={token}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=3600{secure}')
            if self.path == '/api/ai/lock':
                intelligence.logout(self.session())
                return self.send(200, {'ok': True}, cookie='matchup_ai=; HttpOnly; SameSite=Strict; Path=/api; Max-Age=0')
            if not intelligence.authenticated(self.session()):
                raise IntelligenceError('Unlock Matchup Intelligence first.', 401)
            if not intelligence.configured():
                raise IntelligenceError('Configure the Azure API key and enable analysis in .env, then recreate the container.', 503)
            q = {k: [str(v)] for k, v in body.items()}
            league = validated(q, 'league', r'\d+\.l\.\d+')
            team = validated(q, 'team', r'\d+\.l\.\d+\.t\.\d+')
            week = validated(q, 'week', r'(?:[1-9]|1[0-8])')
            match = yahoo.matchup(league, team, week)
            if not match['teams']:
                raise APIError('No matchup is available for analysis.')
            self.send(200, intelligence.analyze(analysis_payload(match, league)))
        except IntelligenceError as e:
            self.send(e.status, {'error': str(e)})
        except APIError as e:
            self.send(400, {'error': str(e)})
        except Exception:
            self.send(502, {'error': 'Analytics unavailable. Check your Azure configuration and connection.'})


if __name__ == '__main__':
    print('Fantasy matchup dashboard listening on port ' + os.getenv('PORT', '8000'), flush=True)
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '8000'))), Handler).serve_forever()
