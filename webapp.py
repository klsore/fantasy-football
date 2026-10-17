"""Hardened public/private WSGI applications; request paths never call Yahoo."""
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from flask import Flask, request, jsonify, g, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from accounts import Accounts
import snapshots

ROOT = Path(__file__).parent


def create_app(mode=None, state_dir=None):
    mode = mode or os.getenv('SITE_MODE','public')
    private = mode=='private'
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=4096, TRUSTED_HOSTS=[os.getenv('PRIVATE_HOST' if private else 'PUBLIC_HOST', 'admin.fantasy.localhost' if private else 'fantasy.localhost')])
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    state = Path(state_dir or os.getenv('DATA_DIR','/app/data'))
    accounts = Accounts(state/('accounts.sqlite3' if private else 'public-limits.sqlite3'))
    app.accounts = accounts
    if private:
        accounts.bootstrap(os.getenv('ADMIN_USERNAME',''), os.getenv('ADMIN_PASSWORD',''))
        from intelligence import Intelligence
        ai = Intelligence(state)
        # Preserve the existing usage ledger rather than resetting its budget.
        ai.file = state/'ai-usage.json'
        ai.settings_provider=accounts.ai_settings
    snapshot_path = state/'private-snapshot.json' if private else Path(os.getenv('SNAPSHOT_DIR','/snapshots'))/'public.json'
    cookie = '__Host-matchup'

    def fail(message, code):
        return jsonify(error=message),code

    @app.before_request
    def guard():
        if request.path=='/api/health':
            return None
        if not request.is_secure:
            return fail('HTTPS is required.',400)
        ip = request.remote_addr or 'unknown'
        if accounts.limited('ip:'+ip,120,60) or accounts.limited('global',1200,60):
            return fail('Too many requests. Please wait a minute.',429)
        g.user = accounts.session(request.cookies.get(cookie,'')) if private else None
        if request.method not in ('GET','HEAD','OPTIONS'):
            if not private:
                return fail('Public site is read-only.',405)
            if request.headers.get('Origin') != request.host_url.rstrip('/'):
                return fail('Invalid request origin.',403)
            if not request.is_json:
                return fail('JSON required.',415)
            if not isinstance(request.get_json(silent=True),dict):
                return fail('A JSON object is required.',400)
            if request.path!='/api/auth/login':
                if not g.user:
                    return fail('Sign in required.',401)
                if not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),g.user['csrf']):
                    return fail('Invalid CSRF token.',403)
        if private and request.path.startswith('/api/') and request.path not in ('/api/auth/login','/api/auth/session') and not g.user:
            return fail('Sign in required.',401)
        if private and not g.user and request.path in ('/','/app.js','/report.js'):
            if request.path=='/':
                return send_from_directory(ROOT/'public','login.html')
            return fail('Sign in required.',401)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Referrer-Policy':'no-referrer','Permissions-Policy':'camera=(), microphone=(), geolocation=()',
            'Strict-Transport-Security':'max-age=31536000','X-Frame-Options':'DENY'})
        if response.status_code==429:
            response.headers['Retry-After']='60'
        return response

    @app.errorhandler(Exception)
    def errors(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):
            return fail(error.name,error.code)
        return fail('Request could not be completed.',500)

    def snap():
        try:
            return snapshots.load(snapshot_path)
        except (ValueError,OSError):
            from werkzeug.exceptions import ServiceUnavailable
            raise ServiceUnavailable()

    def match(s, league=None, team=None, week=None):
        league = league if league is not None else request.args.get('league')
        team = team if team is not None else request.args.get('team')
        week = str(week if week is not None else request.args.get('week',''))
        if league!=s['league']['key']:
            raise ValueError('Only the configured cached matchup is available.')
        matches = s['matchups'] if team==s['team']['key'] else s.get('teamMatchups',{}).get(team,{}) if private else {}
        if week not in matches:
            raise ValueError('That week is not cached.')
        return matches[week]

    @app.get('/api/health')
    def health():
        return jsonify(ok=True)

    @app.get('/api/config')
    def config():
        return jsonify(mode=mode, cachedOnly=True)

    @app.get('/api/auth/session')
    def auth_session():
        if not private:
            return jsonify(authenticated=False,mode=mode)
        return jsonify(authenticated=bool(g.user),user=g.user,mode=mode)

    if private:
        @app.post('/api/auth/login')
        def login():
            body=request.get_json()
            username=str(body.get('username','')).strip().lower()
            password=body.get('password','')
            ip=request.remote_addr or 'unknown'
            if accounts.limited('login-ip:'+ip,5,900) or accounts.limited('login-user:'+username[:64],5,900):
                return fail('Too many login attempts. Wait 15 minutes.',429)
            if not isinstance(password,str) or len(password)>512 or not re.fullmatch(r'[a-z0-9_.-]{1,64}',username):
                return fail('Invalid username or password.',401)
            token=accounts.login(username,password)
            if not token:
                return fail('Invalid username or password.',401)
            result=jsonify(ok=True)
            result.set_cookie(cookie,token,secure=True,httponly=True,samesite='Strict',max_age=Accounts.SESSION_MAX_AGE,path='/')
            return result

        @app.post('/api/auth/logout')
        def logout():
            accounts.logout(request.cookies.get(cookie,''))
            response=jsonify(ok=True)
            response.delete_cookie(cookie,path='/',secure=True,httponly=True,samesite='Strict')
            return response

        @app.post('/api/auth/password')
        def password():
            body=request.get_json()
            old,new=body.get('current',''),body.get('password','')
            if not isinstance(new,str) or not 16<=len(new)<=128:
                return fail('New password must have 16-128 characters.',400)
            if accounts.limited('password:'+str(g.user['id']),5,900):
                return fail('Too many attempts.',429)
            with accounts.db() as db:
                stored=db.execute('SELECT password FROM users WHERE id=?',(g.user['id'],)).fetchone()[0]
                if not isinstance(old,str) or len(old)>512 or not accounts.verify(old,stored):
                    return fail('Current password is incorrect.',403)
                db.execute('UPDATE users SET password=? WHERE id=?',(accounts.hash(new),g.user['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?',(g.user['id'],))
            return jsonify(ok=True)

        @app.route('/api/admin/users',methods=['GET','POST'])
        def users():
            if not g.user['admin']:
                return fail('Admin access required.',403)
            if request.method=='GET':
                return jsonify(accounts.users())
            body=request.get_json()
            username=str(body.get('username','')).strip().lower()
            password=body.get('password','')
            if not re.fullmatch(r'[a-z0-9_.-]{1,64}',username) or not isinstance(password,str) or not 16<=len(password)<=128:
                return fail('Use a valid username and a password of 16-128 characters.',400)
            try:
                accounts.add(username,password,body.get('admin') is True,g.user['username'])
            except sqlite3.IntegrityError:
                return fail('Username already exists.',409)
            return jsonify(ok=True),201

        @app.put('/api/admin/users/<int:uid>/team')
        def assign_team(uid):
            if not g.user['admin']: return fail('Admin access required.',403)
            team=request.get_json().get('team')
            if not isinstance(team,str): return fail('Select a valid team.',400)
            s=snap()
            if team and team not in {t['key'] for t in s.get('teams',[s['team']])}: return fail('Team is not in the cached league.',400)
            try: accounts.assign_team(uid,team,g.user['username'])
            except ValueError as e: return fail(str(e),404)
            return jsonify(ok=True)

        @app.delete('/api/admin/users/<int:uid>')
        def delete_user(uid):
            if not g.user['admin']:
                return fail('Admin access required.',403)
            try:
                accounts.delete(uid,g.user['id'],g.user['username'])
            except ValueError as e:
                return fail(str(e),400)
            return jsonify(ok=True)

        @app.get('/api/manage')
        def management_view():
            import management
            try:
                s=snap()
                if request.args.get('league')!=s['league']['key']: return fail('League not cached.',404)
                result=management.dashboard(s,request.args.get('team'),request.args.get('week'))
                notes=accounts.notes(g.user['id'])
                for note in notes:
                    if note['kind']=='decision':
                        w=str(note['week']); part=s.get('teamMatchups',{}).get(note['team'],{}).get(w,{})
                        data=s.get('weeklyStats',{}).get(w,{})
                        note['review']={'started':data.get(note['player'],{}).get('points'),'alternative':data.get(note['alternative'],{}).get('points')} if part.get('status')=='postevent' and note.get('season')==str(s['league'].get('season','')) else None
                return jsonify(result|{'notes':notes,'refresh':accounts.refresh_status() if g.user['admin'] else None})
            except ValueError as e:
                return fail(str(e),400)

        @app.post('/api/manage/notes')
        def add_management_note():
            b=request.get_json();kind=b.get('kind');s=snap()
            try:
                view=__import__('management').dashboard(s,b.get('team'),b.get('week'))
                roster={p['key'] for p in view['roster']}
                if kind=='watch':
                    if b.get('player') not in {p['key'] for p in view['available']}: raise ValueError('Choose a cached available player.')
                elif kind=='decision':
                    if b.get('player') not in roster or b.get('alternative') not in roster or b['player']==b['alternative']: raise ValueError('Choose two different roster players.')
                else: raise ValueError('Invalid entry type.')
                note={k:str(b.get(k,''))[:500] for k in ('team','week','player','alternative','reason')}
                note['league']=s['league']['key'];note['created']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
                note['season']=str(s['league'].get('season',''))
                note['names']={p['key']:p['name'] for p in view['roster']+view['available'] if p['key'] in (note['player'],note['alternative'])}
                accounts.add_note(g.user['id'],kind,note)
                return jsonify(ok=True),201
            except ValueError as e: return fail(str(e),400)

        @app.delete('/api/manage/notes/<int:nid>')
        def delete_management_note(nid):
            accounts.delete_note(g.user['id'],nid)
            return jsonify(ok=True)

        @app.route('/api/admin/refresh-settings',methods=['GET','POST'])
        def refresh_settings():
            if not g.user['admin']: return fail('Admin access required.',403)
            try:
                hours=request.get_json().get('hours') if request.method=='POST' else None
                if request.method=='POST' and hours is None: return fail('hours is required.',400)
                return jsonify(hours=accounts.refresh_interval(hours,g.user['username']))
            except ValueError as e: return fail(str(e),400)

        @app.route('/api/admin/ai-settings',methods=['GET','POST'])
        def ai_settings():
            if not g.user['admin']: return fail('Admin access required.',403)
            try:
                return jsonify(accounts.ai_settings(request.get_json() if request.method=='POST' else None,g.user['username']))
            except ValueError as e: return fail(str(e),400)

        @app.route('/api/admin/live',methods=['GET','POST'])
        def live_settings():
            if not g.user['admin']: return fail('Admin access required.',403)
            if request.method=='POST':
                value=request.get_json().get('enabled')
                if type(value) is not bool: return fail('enabled must be a boolean.',400)
                return jsonify(accounts.live_state(enabled=value,actor=g.user['username']))
            return jsonify(accounts.live_state())

        @app.post('/api/admin/refresh')
        def queue_refresh():
            if not g.user['admin']: return fail('Admin access required.',403)
            try: accounts.request_refresh(g.user['username'])
            except ValueError as e: return fail(str(e),429)
            return jsonify(ok=True,message='Queued for the Yahoo worker. Check again in a few minutes. No AI request was made.'),202

        @app.get('/api/briefings/<int:report_id>/compare')
        def compare_briefing(report_id):
            import difflib
            report=accounts.briefing(report_id,g.user['id'])
            if not report: return fail('Saved briefing not found.',404)
            saved=report['saved']
            older=accounts.briefings(saved['league'],saved['team'],saved['week'],report_id,g.user['id'])
            if not older: return jsonify(previous=None,changes=[])
            previous=accounts.briefing(older[0]['id'],g.user['id'])
            lines=list(difflib.unified_diff(previous.get('text','').splitlines(),report.get('text','').splitlines(),fromfile=previous['saved']['generated'],tofile=saved['generated'],lineterm=''))
            return jsonify(previous=older[0],changes=lines)

        @app.get('/api/ai/session')
        def ai_session():
            # Site login replaces the former shared AI password gate.
            usage=ai.usage()
            limits=accounts.ai_settings()
            return jsonify(unlocked=True,ready=bool((g.user['admin'] or g.user['team_key']) and os.getenv('AZURE_OPENAI_ENABLED','').lower()=='true' and os.getenv('AZURE_OPENAI_API_KEY')),
                attempts=usage['attempts'],monthlyLimit=limits['monthlyLimit'],
                maxOutputTokens=limits['maxOutputTokens'])

        @app.post('/api/analytics/jobs')
        def analytics_job_start():
            body=request.get_json()
            if not g.user['admin'] and (not g.user['team_key'] or body.get('team')!=g.user['team_key']):
                return fail('Select your assigned team to generate a report.',403)
            try:
                m=match(snap(),body.get('league',''),body.get('team',''),body.get('week',''))
                if not m['teams']: return fail('No matchup available.',400)
                jid=accounts.start_ai_job(g.user['id'])
            except ValueError as e: return fail(str(e),429 if 'running' in str(e) else 400)
            user=dict(g.user)
            def generate():
                try:
                    with app.test_request_context('/api/analytics',method='POST',json=body):
                        g.user=user
                        response=app.make_response(analytics())
                        result=response.get_json()
                        success=response.status_code<400
                    accounts.finish_ai_job(jid,result,success)
                except Exception:
                    accounts.finish_ai_job(jid,{'error':'Report processing failed. Check saved briefings before trying again. No automatic retry was made.'},False)
            import threading
            threading.Thread(target=generate,daemon=True,name='matchup-report').start()
            return jsonify(id=jid,status='running'),202

        @app.get('/api/analytics/jobs/<jid>')
        def analytics_job_status(jid):
            result=accounts.ai_job(jid,g.user['id'])
            return jsonify(result) if result else fail('Report request not found.',404)

        @app.post('/api/analytics')
        def analytics():
            body=request.get_json()
            if not g.user['admin'] and (not g.user['team_key'] or body.get('team')!=g.user['team_key']):
                return fail('An admin must assign your team. You may generate analysis for your assigned team only.',403)
            s=snap()
            try:
                m=match(s,body.get('league',''),body.get('team',''),body.get('week',''))
            except ValueError:
                return fail('Select a cached matchup.',400)
            if not m['teams']:
                return fail('No matchup available.',400)
            teams=[]
            for t in m['teams']:
                players=[]
                for p in t['players']:
                    total=s['seasonStats'].get(p['key'],{})
                    players.append({k:p.get(k) for k in ('name','position','slot','status','statusFull','injuryNote','eligiblePositions','bye')} | {'weeklyPoints':p['points'],'seasonPoints':total.get('points'),'seasonStats':total.get('stats',[])})
                teams.append({'side':'your team' if t['mine'] else 'opponent','weeklyPoints':t['points'],'projectedPoints':t['projected'],'players':players})
            from intelligence import IntelligenceError
            try:
                result=ai.analyze({'week':m['week'],'status':m['status'],'teams':teams,'statLabels':s['statLabels'],
                    'retrievedAtUTC':s['updated'],'freshness':'Daily cached Yahoo snapshot; not live news. Current metadata may not represent historical-week injuries.'})
                import hashlib
                try:
                    report_id=accounts.save_briefing(s['league']['key'],body['team'],m['week'],s['league'].get('season',''),s['updated'],g.user['username'],
                        os.getenv('AZURE_OPENAI_DEPLOYMENT',''),hashlib.sha256((ROOT/'ANALYSIS_PROMPT.md').read_bytes()).hexdigest(),[t['name'] for t in m['teams']],result)
                    return jsonify(accounts.briefing(report_id,g.user['id']))
                except (OSError,sqlite3.Error):
                    # Keep the paid result visible even if persistence fails; never retry Azure.
                    return jsonify(result | {'saveError':'Report generated but could not be saved. Copy it before leaving this page.'})
            except IntelligenceError as e:
                return fail(str(e),e.status)

        @app.get('/api/briefings')
        def briefing_list():
            league,team,week=(request.args.get(k,'') for k in ('league','team','week'))
            if not re.fullmatch(r'\d+\.l\.\d+',league) or not re.fullmatch(r'\d+\.l\.\d+\.t\.\d+',team) or not re.fullmatch(r'(?:[1-9]|1[0-8])',week):
                return fail('Select a valid matchup.',400)
            before=request.args.get('before','')
            if before and (not before.isdigit() or len(before)>18):
                return fail('Invalid history cursor.',400)
            rows=accounts.briefings(league,team,week,int(before) if before else None,g.user['id'])
            return jsonify(items=rows[:20],nextBefore=rows[19]['id'] if len(rows)>20 else None)

        @app.get('/api/briefings/<int:report_id>')
        def briefing_detail(report_id):
            result=accounts.briefing(report_id,g.user['id'])
            return jsonify(result) if result else fail('Saved briefing not found.',404)

    @app.get('/api/live')
    def live_status():
        path=state/'live.json' if private else Path(os.getenv('SNAPSHOT_DIR','/snapshots'))/'live.json'
        try: return jsonify(json.loads(path.read_text()))
        except (OSError,ValueError): return jsonify(enabled=False,active=False)

    @app.get('/api/leagues')
    def leagues():
        return jsonify([snap()['league']])

    @app.get('/api/teams')
    def teams():
        s=snap()
        if request.args.get('league')!=s['league']['key']:
            return fail('League is not published.',404)
        available = s.get('teams',[s['team']]) if private else [s['team']]
        return jsonify([{k:v for k,v in t.items() if k!='manager'} for t in available])

    @app.get('/api/matchup')
    def matchup():
        s=snap()
        try:
            result=dict(match(s))
        except ValueError as e:
            return fail(str(e),404)
        result['updated']=s['updated']
        result['snapshot']=True
        result['scoresUpdated']=s.get('scoresUpdated',s['updated'])
        return jsonify(result)

    @app.get('/api/player-stats')
    def stats():
        s=snap()
        try:
            if request.args.get('league')!=s['league']['key']:
                raise ValueError('League not published.')
            mode=request.args.get('mode')
            if mode not in ('season','week','range'):
                raise ValueError('Invalid timeframe.')
            start=int(request.args.get('start','1'));end=int(request.args.get('end',str(start))) if mode=='range' else start
            if not 1<=start<=end<=18:
                raise ValueError('Invalid week range.')
            return jsonify(snapshots.player_stats(s,request.args.get('player'),mode,start,end))
        except (ValueError,TypeError) as e:
            return fail(str(e),404)

    @app.get('/login')
    def login_page():
        return send_from_directory(ROOT/'public','login.html')

    @app.get('/')
    def index():
        return send_from_directory(ROOT/'public','index.html')

    @app.get('/<name>')
    def asset(name):
        if name not in ('app.js','report.js','style.css','auth.js','login.js','manage.js'):
            return fail('Not found.',404)
        return send_from_directory(ROOT/'public',name)

    return app
