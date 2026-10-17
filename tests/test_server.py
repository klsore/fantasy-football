import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import server


class YahooTests(unittest.TestCase):
    def test_player_season_uses_league_scoring_endpoint(self):
        y = server.Yahoo()
        settings = server.xml(b'<f><stat_categories><stats><stat><stat_id>4</stat_id><display_name>Pass Yds</display_name></stat></stats></stat_categories></f>')
        player = server.xml(b'<f><players><player><player_points><total>78.43</total></player_points><player_stats><stats><stat><stat_id>4</stat_id><value>1234</value></stat></stats></player_stats></player></players></f>')
        with patch.object(y, 'get', side_effect=[settings, player]) as get:
            result = y.player_stats('1.l.2', '1.p.3', 'season')
        self.assertEqual(result['points'], '78.43')
        self.assertFalse(result['allZero'])
        self.assertEqual(get.call_args.args[0], 'league/1.l.2/players;player_keys=1.p.3/stats;type=season')

    def test_range_sums_counts_but_not_rates_or_missing_values(self):
        y = server.Yahoo()
        settings = server.xml(b'<f><stat_categories><stats><stat><stat_id>4</stat_id><display_name>Pass Yds</display_name></stat><stat><stat_id>8</stat_id><display_name>Avg</display_name></stat></stats></stat_categories></f>')
        one = server.xml(b'<f><player><player_points><total>1.10</total></player_points><player_stats><stats><stat><stat_id>4</stat_id><value>100</value></stat><stat><stat_id>8</stat_id><value>5.2</value></stat></stats></player_stats></player></f>')
        two = server.xml(b'<f><player><player_stats><stats><stat><stat_id>4</stat_id><value>-</value></stat></stats></player_stats></player></f>')
        with patch.object(y, 'get', side_effect=[settings, one, two]):
            result = y.player_stats('1.l.2', '1.p.3', 'range', 1, 2)
        self.assertIsNone(result['points'])
        self.assertEqual(result['stats'], [{'id':'4', 'value':None}])
        self.assertEqual(result['omitted'], ['Avg'])

    def test_zero_week_and_invalid_timeframes(self):
        y = server.Yahoo()
        zero = server.xml(b'<f><player><player_stats><stats><stat><stat_id>4</stat_id><value>0</value></stat></stats></player_stats></player></f>')
        with patch.object(y, 'get', return_value=zero):
            self.assertTrue(y.player_stats('1.l.2', '1.p.3', 'week', 6, 6)['allZero'])
            with self.assertRaises(server.APIError):
                y.player_stats('1.l.2', '1.p.3', 'range', 6, 2)
        with self.assertRaises(server.APIError):
            y.player_stats('1.l.2', '9.p.3', 'season')

    def test_namespaced_player_and_missing_points(self):
        root = server.xml(b'<fantasy_content xmlns="urn:yahoo"><player><player_key>1.p.2</player_key><name><full>Test Player</full></name><player_stats><stats><stat><stat_id>4</stat_id><value>123</value></stat></stats></player_stats></player></fantasy_content>')
        p = server.Yahoo.player(root.find('player'))
        self.assertEqual(p['name'], 'Test Player')
        self.assertIsNone(p['points'])
        self.assertEqual(p['stats'], [{'id': '4', 'value': '123'}])

    def test_matchup_finds_owned_team_and_labels(self):
        y = server.Yahoo()
        board = server.xml(b'<f><scoreboard><matchups><matchup><status>midevent</status><teams><team><team_key>1.l.2.t.4</team_key><name>Other</name></team><team><team_key>1.l.2.t.3</team_key><name>Mine</name></team></teams></matchup></matchups></scoreboard></f>')
        settings = server.xml(b'<f><stat_categories><stats><stat><stat_id>4</stat_id><display_name>Pass Yds</display_name></stat></stats></stat_categories></f>')
        with patch.object(y, 'teams', return_value=[{'key':'1.l.2.t.3'}]), patch.object(y, 'roster', return_value=[]), patch.object(y, 'get', side_effect=[board, settings]):
            match = y.matchup('1.l.2', '1.l.2.t.3', '6')
        self.assertEqual(match['teams'][0]['name'], 'Mine')
        self.assertEqual(match['statLabels']['4'], 'Pass Yds')

    def test_bye_week(self):
        y = server.Yahoo()
        with patch.object(y, 'teams', return_value=[{'key':'1.l.2.t.3'}]), patch.object(y, 'get', return_value=server.xml(b'<f/>')):
            self.assertEqual(y.matchup('1.l.2', '1.l.2.t.3', '6')['teams'], [])

    def test_rejects_path_injection(self):
        for invalid in ('1.l.2/teams', '../.env', '1.l.2;out=teams'):
            with self.assertRaises(server.APIError):
                server.validated({'league':[invalid]}, 'league', r'\d+\.l\.\d+')

    def test_rotated_refresh_token_is_persisted_and_reused(self):
        y = server.Yahoo()
        with tempfile.TemporaryDirectory(dir=server.ROOT / 'data') as folder, patch.dict(server.os.environ, {'YAHOO_CLIENT_ID':'test', 'YAHOO_CLIENT_SECRET':'test', 'YAHOO_REFRESH_TOKEN':'initial'}):
            y.token_file = Path(folder) / 'token.json'
            responses = [io.BytesIO(json.dumps({'access_token':'access', 'refresh_token':'rotated', 'expires_in':3600}).encode()), io.BytesIO(b'{"access_token":"next","expires_in":3600}')]
            with patch('urllib.request.urlopen', side_effect=responses) as network:
                self.assertEqual(y.access_token(), 'access')
                self.assertEqual(y.access_token(), 'access')
                self.assertEqual(network.call_count, 1)
                y.expires = 0
                y.access_token()
                self.assertIn(b'refresh_token=rotated', network.call_args.args[0].data)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def test_health_and_secret_file_not_served(self):
        with urllib.request.urlopen(self.base + '/api/health') as r:
            self.assertEqual(json.load(r), {'ok':True})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + '/.env')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_cross_origin_analytics_blocked(self):
        req = urllib.request.Request(self.base + '/api/analytics', b'{}', {'Origin':'https://example.com'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()

