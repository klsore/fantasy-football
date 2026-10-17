"""Password sessions and persistent request limits for on-demand Azure analysis."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class IntelligenceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Intelligence:
    def __init__(self, root):
        self.file = Path(root) / 'data' / 'ai-usage.json'
        self.lock = threading.Lock()
        self.running = threading.Lock()
        self.sessions = {}
        self.failures = {}

    def password_digest(self):
        password = os.getenv('MATCHUP_AI_PASSWORD', '')
        return hashlib.sha256(password.encode()).digest() if len(password) >= 16 else None

    def authenticated(self, token):
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v[0] > now}
            session = self.sessions.get(token)
            return bool(session and session[1] == self.password_digest())

    def login(self, password, ip):
        with self.lock:
            now = time.time()
            self.failures = {k: v for k, v in self.failures.items() if now - v[0] < 900}
            failures = self.failures.get(ip, (now, 0))
            if failures[1] >= 5:
                raise IntelligenceError('Too many unlock attempts. Try again in 15 minutes.', 429)
            digest = self.password_digest()
            if not digest:
                raise IntelligenceError('Set MATCHUP_AI_PASSWORD in .env to at least 16 characters.', 503)
            if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(), digest):
                self.failures[ip] = (failures[0], failures[1] + 1)
                raise IntelligenceError('Incorrect password.', 401)
            self.failures.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = (now + 3600, digest)
            return token

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def configured(self):
        return bool(os.getenv('AZURE_OPENAI_ENABLED', '').lower() == 'true'
                    and os.getenv('AZURE_OPENAI_API_KEY')
                    and os.getenv('AZURE_OPENAI_ENDPOINT') and os.getenv('AZURE_OPENAI_DEPLOYMENT'))

    def usage(self):
        month = time.strftime('%Y-%m', time.gmtime())
        state = json.loads(self.file.read_text()) if self.file.exists() else {}
        if state.get('month') != month:
            return {'month': month, 'attempts': 0, 'last': state.get('last', 0)}
        return state

    def status(self, token):
        unlocked = self.authenticated(token)
        result = {'unlocked': unlocked}
        if unlocked:
            with self.lock:
                state = self.usage()
            result.update(ready=self.configured(), attempts=state['attempts'],
                          monthlyLimit=int(os.getenv('AI_MONTHLY_REQUEST_LIMIT', '20')),
                          maxOutputTokens=int(os.getenv('AI_MAX_OUTPUT_TOKENS', '4096')))
        return result

    def limits(self):
        provider=getattr(self,'settings_provider',None)
        return provider() if provider else {'monthlyLimit':int(os.getenv('AI_MONTHLY_REQUEST_LIMIT','20')),'maxOutputTokens':int(os.getenv('AI_MAX_OUTPUT_TOKENS','4096'))}

    def analyze(self, payload):
        if not self.configured():
            raise IntelligenceError('Set the Azure API key and enable Azure analysis in .env, then recreate the container.', 503)
        endpoint = os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith(('.services.ai.azure.com', '.openai.azure.com')) or parsed.query or parsed.fragment:
            raise IntelligenceError('Configure an HTTPS Azure OpenAI resource endpoint.')
        if not endpoint.endswith('/openai/v1/responses'):
            raise IntelligenceError('AZURE_OPENAI_ENDPOINT must end with /openai/v1/responses.')
        data = json.dumps(payload, separators=(',', ':'))
        if len(data.encode()) > 40000:
            raise IntelligenceError('Matchup data is too large for the configured analysis input limit.')
        limits=self.limits()
        max_tokens = limits['maxOutputTokens']
        if not 512 <= max_tokens <= 8192:
            raise IntelligenceError('AI_MAX_OUTPUT_TOKENS must be between 512 and 8192.')
        body = {'model': os.environ['AZURE_OPENAI_DEPLOYMENT'], 'store': False,
                'reasoning': {'effort': 'low'}, 'max_output_tokens': max_tokens,
                'instructions': (Path(__file__).parent / 'ANALYSIS_PROMPT.md').read_text(encoding='utf-8'),
                'input': data}
        if not self.running.acquire(blocking=False):
            raise IntelligenceError('An analysis is already running. Wait for it to finish.', 429)
        try:
            with self.lock:
                state = self.usage()
                if state['attempts'] >= limits['monthlyLimit']:
                    raise IntelligenceError('Monthly analysis request limit reached. Ask an admin to review AI budget settings.', 429)
                if time.time() - state['last'] < int(os.getenv('AI_COOLDOWN_SECONDS', '60')):
                    raise IntelligenceError('Please wait at least one minute between analyses.', 429)
                # Reserve BEFORE contacting Azure: errors/timeouts may still be billed.
                state['attempts'] += 1
                state['last'] = time.time()
                self.file.parent.mkdir(exist_ok=True)
                temp = self.file.with_suffix('.tmp')
                temp.write_text(json.dumps(state))
                temp.replace(self.file)
            req = urllib.request.Request(endpoint, json.dumps(body).encode(),
                                         {'api-key': os.environ['AZURE_OPENAI_API_KEY'], 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as e:
                detail=''
                try:
                    code=json.loads(e.read(4096)).get('error',{}).get('code','')
                    if isinstance(code,str) and code and len(code)<80 and all(c.isalnum() or c in '_-.' for c in code): detail=' ('+code+')'
                except (ValueError,AttributeError,TypeError): pass
                hint=' Azure reported temporary unavailability; wait a few minutes before a manual retry.' if e.code in (429,502,503,504) else ''
                raise IntelligenceError(f'Azure returned HTTP {e.code}{detail}.{hint} No automatic retry was made; this attempt counts toward the shared limit.',502) from None
            except (urllib.error.URLError, TimeoutError):
                raise IntelligenceError('Azure connection timed out or failed. No retry was made. This attempt may have been billed.', 502) from None
            text = '\n'.join(c.get('text', '') for item in result.get('output', [])
                             if item.get('type') == 'message' for c in item.get('content', [])
                             if c.get('type') == 'output_text')
            if not text:
                raise IntelligenceError('Azure returned no analysis text (possibly the reasoning/output limit). No retry was made.', 502)
            return {'text': text, 'usage': result.get('usage', {}), 'attempts': state['attempts'],
                    'incomplete': result.get('status') == 'incomplete'}
        finally:
            self.running.release()
