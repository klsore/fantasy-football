"""SQLite users, opaque sessions, CSRF tokens, and persistent request limits."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Accounts:
    SESSION_MAX_AGE = 30 * 86400
    SESSION_IDLE_AGE = 7 * 86400

    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS limits(key TEXT PRIMARY KEY, start REAL NOT NULL, count INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS briefings(id INTEGER PRIMARY KEY, league TEXT NOT NULL, team TEXT NOT NULL, week TEXT NOT NULL, season TEXT NOT NULL, generated TEXT NOT NULL, snapshot_at TEXT NOT NULL, creator TEXT NOT NULL, model TEXT NOT NULL, prompt_hash TEXT NOT NULL, team_names TEXT NOT NULL, report TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS ai_jobs(id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, result TEXT);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS refresh_jobs(id INTEGER PRIMARY KEY, requested REAL NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, finished REAL);
              CREATE INDEX IF NOT EXISTS briefing_matchup ON briefings(league,team,week,id DESC);
            ''')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if 'last_seen' not in {r[1] for r in db.execute('PRAGMA table_info(sessions)')}:
                db.execute('ALTER TABLE sessions ADD COLUMN last_seen REAL NOT NULL DEFAULT 0')
                db.execute('UPDATE sessions SET last_seen=?',(time.time(),))
            if 'team_key' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute("ALTER TABLE users ADD COLUMN team_key TEXT NOT NULL DEFAULT ''")
            if 'owner_id' not in {r[1] for r in db.execute('PRAGMA table_info(briefings)')}:
                db.execute('ALTER TABLE briefings ADD COLUMN owner_id INTEGER')
                db.execute('UPDATE briefings SET owner_id=(SELECT id FROM users WHERE username=briefings.creator)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def hash(password):
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864).hex()
        return salt + ':' + digest

    @staticmethod
    def verify(password, stored):
        salt, expected = stored.split(':')
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864).hex()
        return hmac.compare_digest(digest, expected)

    def bootstrap(self, username, password):
        import re
        with self.db() as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                return
            if not re.fullmatch(r'[a-z0-9_.-]{1,64}',username) or not 16 <= len(password) <= 128:
                raise ValueError('Set ADMIN_USERNAME and ADMIN_PASSWORD (at least 16 characters) in .env.')
            db.execute('INSERT INTO users(username,password,admin) VALUES(?,?,1)', (username, self.hash(password)))

    def limited(self, key, maximum, seconds):
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM limits WHERE start < ?', (now - 86400,))
            db.execute('INSERT INTO limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET start=CASE WHEN start<? THEN excluded.start ELSE start END, count=CASE WHEN start<? THEN 1 ELSE count+1 END', (key, now, now-seconds, now-seconds))
            row = db.execute('SELECT count FROM limits WHERE key=?', (key,)).fetchone()
            return row['count'] > maximum

    def login(self, username, password):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            # Same expensive hash path for unknown accounts.
            stored = row['password'] if row else '00'*16 + ':' + '00'*64
            valid = self.verify(password, stored)
            if not row or not valid:
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO sessions(token,user_id,csrf,expires,last_seen) VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, time.time()+self.SESSION_MAX_AGE, time.time()))
            db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)', (time.time(), username, 'login'))
            return token

    def session(self, token):
        if not token: return None
        now=time.time();digest=hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            row=db.execute('SELECT users.id,username,admin,team_key,csrf,expires FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>? AND last_seen>?',(digest,now,now-self.SESSION_IDLE_AGE)).fetchone()
            if row:
                # Throttle writes from polling while retaining sliding inactivity expiry.
                db.execute('UPDATE sessions SET last_seen=? WHERE token=? AND last_seen<?',(now,digest,now-300))
                return dict(row)
            db.execute('DELETE FROM sessions WHERE token=?',(digest,))
            return None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,username,admin,team_key FROM users ORDER BY username')]

    def save_briefing(self, league, team, week, season, snapshot_at, creator, model, prompt_hash, team_names, report):
        import json
        generated=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        with self.db() as db:
            cursor=db.execute('INSERT INTO briefings(league,team,week,season,generated,snapshot_at,creator,model,prompt_hash,team_names,report,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,(SELECT id FROM users WHERE username=?))',
                (league,team,str(week),str(season),generated,snapshot_at,creator,model,prompt_hash,json.dumps(team_names),json.dumps(report),creator))
            return cursor.lastrowid

    def briefings(self, league, team, week, before=None, owner_id=None):
        with self.db() as db:
            rows=db.execute('SELECT id,league,team,week,season,generated,snapshot_at,creator,model,team_names FROM briefings WHERE league=? AND team=? AND week=? AND id<? AND (? IS NULL OR owner_id=?) ORDER BY id DESC LIMIT 21',
                (league,team,str(week),before or 9223372036854775807,owner_id,owner_id)).fetchall()
            return [dict(r) for r in rows]

    def briefing(self, report_id, owner_id=None):
        import json
        with self.db() as db:
            row=db.execute('SELECT * FROM briefings WHERE id=? AND (? IS NULL OR owner_id=?)',(report_id,owner_id,owner_id)).fetchone()
        if not row:
            return None
        saved=dict(row)
        report=json.loads(saved.pop('report'))
        return report | {'saved':saved}

    def add(self, username, password, admin, actor):
        with self.db() as db:
            db.execute('INSERT INTO users(username,password,admin) VALUES(?,?,?)', (username, self.hash(password), int(admin)))
            db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)', (time.time(), actor, 'create user '+username))

    def delete(self, uid, actor_id, actor):
        if uid == actor_id:
            raise ValueError('You cannot delete your current account.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not user:
                raise ValueError('User not found.')
            if user['admin'] and db.execute('SELECT COUNT(*) FROM users WHERE admin=1').fetchone()[0] <= 1:
                raise ValueError('Cannot delete the last admin.')
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            db.execute('DELETE FROM notes WHERE user_id=?', (uid,))
            db.execute('DELETE FROM briefings WHERE owner_id=?', (uid,))
            db.execute('DELETE FROM ai_jobs WHERE owner_id=?', (uid,))
            db.execute('DELETE FROM users WHERE id=?', (uid,))
            db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)', (time.time(), actor, 'delete user '+user['username']))

    def notes(self, uid):
        import json
        with self.db() as db:
            return [dict(id=r['id'],kind=r['kind'],**json.loads(r['payload'])) for r in db.execute('SELECT * FROM notes WHERE user_id=? ORDER BY id DESC LIMIT 200',(uid,))]

    def add_note(self, uid, kind, payload):
        import json
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM notes WHERE user_id=?',(uid,)).fetchone()[0]>=200:
                raise ValueError('Delete an entry before adding more (200 maximum).')
            db.execute('INSERT INTO notes(user_id,kind,payload) VALUES(?,?,?)',(uid,kind,json.dumps(payload)))

    def delete_note(self, uid, nid):
        with self.db() as db:
            db.execute('DELETE FROM notes WHERE id=? AND user_id=?',(nid,uid))

    def request_refresh(self, actor):
        now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM refresh_jobs WHERE status IN ('queued','running') AND requested>?",(now-3600,)).fetchone():
                raise ValueError('A refresh is already queued or running.')
            if db.execute('SELECT 1 FROM refresh_jobs WHERE requested>?',(now-900,)).fetchone():
                raise ValueError('Allow 15 minutes between manual refreshes.')
            if db.execute('SELECT COUNT(*) FROM refresh_jobs WHERE requested>?',(now-86400,)).fetchone()[0]>=4:
                raise ValueError('Four manual refreshes per rolling 24 hours are allowed.')
            db.execute("INSERT INTO refresh_jobs(requested,actor,status) VALUES(?,?,'queued')",(now,actor))

    def refresh_status(self):
        with self.db() as db:
            row=db.execute('SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def claim_refresh(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE refresh_jobs SET status='interrupted',finished=? WHERE status IN ('running','queued') AND requested<?",(time.time(),time.time()-3600))
            row=db.execute("SELECT id FROM refresh_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE refresh_jobs SET status='running' WHERE id=?",(row['id'],))
                return row['id']

    def finish_refresh(self, jid, success):
        with self.db() as db:
            db.execute('UPDATE refresh_jobs SET status=?,finished=? WHERE id=?',('complete' if success else 'failed',time.time(),jid))

    def live_state(self, enabled=None, actor='worker', reserve_calls=0):
        import json
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM settings WHERE key='live'").fetchone()
            s=json.loads(row[0]) if row else {'enabled':False,'nextPoll':0,'failures':0,'calls':0}
            day=time.strftime('%Y-%m-%d',time.gmtime())
            if s.get('day')!=day: s.update(day=day,calls=0)
            if enabled is not None:
                s['enabled']=enabled
                db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)',(time.time(),actor,'live scoring '+str(enabled)))
            s['reserved']=False
            if reserve_calls and s['enabled'] and time.time()>=s.get('nextPoll',0) and s['calls']+reserve_calls<=2000:
                s.update(calls=s['calls']+reserve_calls,nextPoll=time.time()+300,reserved=True)
            db.execute("INSERT INTO settings VALUES('live',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(s),))
            return s

    def live_finished(self, success):
        import json
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM settings WHERE key='live'").fetchone()
            s=json.loads(row[0]);s['failures']=0 if success else s.get('failures',0)+1
            if success: s['lastSuccess']=time.time()
            s['lastResult']='updated' if success else 'error; retaining previous scores'
            s['nextPoll']=time.time()+min(1800,300*2**min(s['failures'],3))
            db.execute("UPDATE settings SET value=? WHERE key='live'",(json.dumps(s),))

    def refresh_interval(self, hours=None, actor='worker'):
        if hours is not None and (type(hours) is not int or not 1<=hours<=24):
            raise ValueError('Choose a refresh interval from 1 to 24 hours.')
        with self.db() as db:
            if hours is not None:
                db.execute("INSERT INTO settings VALUES('refresh_hours',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(hours),))
                db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)',(time.time(),actor,'full refresh interval '+str(hours)))
            row=db.execute("SELECT value FROM settings WHERE key='refresh_hours'").fetchone()
            return int(row[0]) if row else 3

    def assign_team(self, uid, team_key, actor):
        with self.db() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
                raise ValueError('User not found.')
            db.execute('UPDATE users SET team_key=? WHERE id=?',(team_key,uid))
            db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)',(time.time(),actor,'assign team to user '+str(uid)))

    def ai_settings(self, values=None, actor='worker'):
        import json
        if values is not None:
            if set(values)!={'monthlyLimit','maxOutputTokens'} or type(values['monthlyLimit']) is not int or not 0<=values['monthlyLimit']<=10000 or type(values['maxOutputTokens']) is not int or not 512<=values['maxOutputTokens']<=8192:
                raise ValueError('Monthly attempts must be 0–10000; output/reasoning tokens must be 512–8192.')
        with self.db() as db:
            if values is not None:
                db.execute("INSERT INTO settings VALUES('ai_limits',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(values),))
                db.execute('INSERT INTO audit(at,actor,action) VALUES(?,?,?)',(time.time(),actor,'update AI budget limits'))
            row=db.execute("SELECT value FROM settings WHERE key='ai_limits'").fetchone()
            return json.loads(row[0]) if row else {'monthlyLimit':int(os.getenv('AI_MONTHLY_REQUEST_LIMIT','20')),'maxOutputTokens':int(os.getenv('AI_MAX_OUTPUT_TOKENS','4096'))}

    def start_ai_job(self, owner):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE ai_jobs SET status='interrupted' WHERE status='running' AND created<?",(time.time()-300,))
            if db.execute("SELECT 1 FROM ai_jobs WHERE status='running'").fetchone():
                raise ValueError('An analysis is already running. Wait for it to finish.')
            jid=secrets.token_urlsafe(24)
            db.execute("INSERT INTO ai_jobs VALUES(?,?,?,'running',NULL)",(jid,owner,time.time()))
            return jid

    def finish_ai_job(self, jid, result, success):
        import json
        with self.db() as db:
            db.execute('UPDATE ai_jobs SET status=?,result=? WHERE id=?',('complete' if success else 'failed',json.dumps(result),jid))

    def ai_job(self, jid, owner):
        import json
        with self.db() as db:
            row=db.execute('SELECT * FROM ai_jobs WHERE id=? AND owner_id=?',(jid,owner)).fetchone()
            if not row: return None
            status=row['status']
            if status=='running' and row['created']<time.time()-300: status='interrupted'
            return {'id':jid,'status':status,'result':json.loads(row['result']) if row['result'] else None}
