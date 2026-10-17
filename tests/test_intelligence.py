import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from intelligence import Intelligence, IntelligenceError
import server


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ai = Intelligence(self.temp.name)
        self.env = patch.dict(os.environ, {'MATCHUP_AI_PASSWORD':'test-password-long-enough',
            'AZURE_OPENAI_ENABLED':'true', 'AZURE_OPENAI_API_KEY':'fake-key',
            'AZURE_OPENAI_DEPLOYMENT':'gpt-6.1-sol',
            'AZURE_OPENAI_ENDPOINT':'https://test.services.ai.azure.com/openai/v1/responses',
            'AI_MONTHLY_REQUEST_LIMIT':'2', 'AI_COOLDOWN_SECONDS':'0', 'AI_MAX_OUTPUT_TOKENS':'4096'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_password_session_expiry_revocation_and_no_network(self):
        with patch('urllib.request.urlopen') as network:
            token = self.ai.login('test-password-long-enough', 'local')
            self.assertTrue(self.ai.status(token)['unlocked'])
            with patch.dict(os.environ, {'MATCHUP_AI_PASSWORD':'a-different-long-password'}):
                self.assertFalse(self.ai.authenticated(token))
            self.ai.logout(token)
            self.assertFalse(self.ai.authenticated(token))
            token = self.ai.login('test-password-long-enough', 'local')
            with patch('time.time', return_value=99999999999):
                self.assertFalse(self.ai.authenticated(token))
            network.assert_not_called()

    def test_password_attempt_limit(self):
        for _ in range(5):
            with self.assertRaises(IntelligenceError) as e:
                self.ai.login('wrong', 'local')
            self.assertEqual(e.exception.status, 401)
        with self.assertRaises(IntelligenceError) as e:
            self.ai.login('test-password-long-enough', 'local')
        self.assertEqual(e.exception.status, 429)

    def response(self):
        return io.BytesIO(json.dumps({'status':'completed', 'output':[{'type':'reasoning'},
            {'type':'message','content':[{'type':'output_text','text':'Test analysis'}]}],
            'usage':{'input_tokens':100,'output_tokens':200}}).encode())

    def test_admin_settings_control_request_without_resetting_usage(self):
        limits={'monthlyLimit':5,'maxOutputTokens':1024}
        self.ai.settings_provider=lambda:limits
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k:self.response()) as network:
            self.ai.analyze({'week':6})
            self.assertEqual(json.loads(network.call_args.args[0].data)['max_output_tokens'],1024)
            limits['monthlyLimit']=0
            with self.assertRaises(IntelligenceError): self.ai.analyze({'week':6})
            self.assertEqual(network.call_count,1)
            self.assertEqual(self.ai.usage()['attempts'],1)
            limits.update(monthlyLimit=5,maxOutputTokens=2048)
            self.ai.analyze({'week':6})
            self.assertEqual(json.loads(network.call_args.args[0].data)['max_output_tokens'],2048)
            self.assertEqual(self.ai.usage()['attempts'],2)

    def test_responses_request_and_persistent_monthly_cap(self):
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k:self.response()) as network:
            result = self.ai.analyze({'week':6})
            self.assertEqual(result['text'], 'Test analysis')
            body = json.loads(network.call_args.args[0].data)
            self.assertEqual(body['model'], 'gpt-6.1-sol')
            self.assertEqual(body['max_output_tokens'], 4096)
            self.assertEqual(body['reasoning'], {'effort':'low'})
            self.assertFalse(body['store'])
            self.assertNotIn('tools', body)
            self.ai.analyze({'week':6})
            other = Intelligence(self.temp.name)
            with self.assertRaises(IntelligenceError):
                other.analyze({'week':6})
            self.assertEqual(network.call_count, 2)

    def test_failed_calls_count_and_no_retry(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('test')) as network:
            with self.assertRaises(IntelligenceError):
                self.ai.analyze({'week':6})
            self.assertEqual(network.call_count, 1)
        self.assertEqual(self.ai.usage()['attempts'], 1)

    def test_cooldown_and_concurrency_block_network(self):
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k:self.response()) as network:
            self.ai.analyze({'week':6})
            with patch.dict(os.environ, {'AI_COOLDOWN_SECONDS':'60'}):
                with self.assertRaises(IntelligenceError):
                    self.ai.analyze({'week':6})
            self.ai.running.acquire()
            try:
                with self.assertRaises(IntelligenceError):
                    self.ai.analyze({'week':6})
            finally:
                self.ai.running.release()
            self.assertEqual(network.call_count, 1)

    def test_disabled_or_oversize_payload_never_calls_azure(self):
        with patch('urllib.request.urlopen') as network:
            with patch.dict(os.environ, {'AZURE_OPENAI_ENABLED':'false'}):
                with self.assertRaises(IntelligenceError):
                    self.ai.analyze({})
            with self.assertRaises(IntelligenceError):
                self.ai.analyze({'data':'x'*41000})
            network.assert_not_called()


class IntelligenceHTTPTests(unittest.TestCase):
    def test_unlock_cookie_and_lock_without_azure_calls(self):
        import threading
        import urllib.request
        from http.cookiejar import CookieJar
        http = server.ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{http.server_port}'
        jar = CookieJar()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def post(path, body):
            return client.open(urllib.request.Request(base+path, json.dumps(body).encode(), {'Content-Type':'application/json'}))
        try:
            with tempfile.TemporaryDirectory() as root, patch.object(server, 'intelligence', Intelligence(root)), patch.dict(os.environ, {'MATCHUP_AI_PASSWORD':'test-password-long-enough'}), patch.object(Intelligence, 'analyze') as azure:
                with post('/api/ai/unlock', {'password':'test-password-long-enough'}) as response:
                    cookie = response.headers['Set-Cookie']
                    self.assertIn('HttpOnly', cookie)
                    self.assertIn('SameSite=Strict', cookie)
                with client.open(base+'/api/ai/session') as response:
                    self.assertTrue(json.load(response)['unlocked'])
                with post('/api/ai/lock', {}):
                    pass
                with client.open(base+'/api/ai/session') as response:
                    self.assertFalse(json.load(response)['unlocked'])
                azure.assert_not_called()
        finally:
            http.shutdown()
            http.server_close()

    def test_unauthenticated_analysis_rejected_before_yahoo_or_azure(self):
        import threading
        import urllib.request
        http = server.ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server.yahoo, 'matchup') as yahoo, patch('urllib.request.urlopen', wraps=urllib.request.urlopen):
                req = urllib.request.Request(f'http://127.0.0.1:{http.server_port}/api/analytics', b'{}', {'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
                yahoo.assert_not_called()
        finally:
            http.shutdown()
            http.server_close()
