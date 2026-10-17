import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from accounts import Accounts
import refresh

class LiveTests(unittest.TestCase):
    def test_windows_and_unknown_times(self):
        s={'nflSchedule':{'6':{'KC':{'kickoff':'2026-10-18T17:00:00+00:00'},'BUF':{'kickoff':None}}}}
        from datetime import datetime
        start=datetime.fromisoformat('2026-10-18T17:00:00+00:00').timestamp()
        self.assertEqual(refresh.active_weeks(s,start-901),[])
        self.assertEqual(refresh.active_weeks(s,start-900),['6'])
        self.assertEqual(refresh.active_weeks(s,start+21601),[])

    def test_persistent_toggle_budget_and_backoff(self):
        with tempfile.TemporaryDirectory() as t:
            a=Accounts(Path(t)/'db')
            self.assertFalse(a.live_state()['enabled'])
            a.live_state(enabled=True,actor='admin')
            self.assertTrue(Accounts(Path(t)/'db').live_state()['enabled'])
            self.assertTrue(a.live_state(reserve_calls=1999)['reserved'])
            self.assertFalse(a.live_state(reserve_calls=2)['reserved'])
            a.live_finished(False)
            self.assertEqual(a.live_state()['failures'],1)
            a.live_state(enabled=False)
            self.assertFalse(a.live_state(reserve_calls=1)['reserved'])

    def test_off_or_outside_window_does_not_call_yahoo(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t);(path/'private-snapshot.json').write_text(json.dumps({'nflSchedule':{}}))
            a=Accounts(path/'db');a.live_state(enabled=True)
            with patch.object(refresh,'DATA',path),patch.object(refresh,'PUBLIC',path/'pub'),patch.object(refresh,'refresh_scores') as fetch:
                refresh.live_tick(a);fetch.assert_not_called()
                self.assertFalse(json.loads((path/'pub/live.json').read_text())['active'])

    def test_score_refresh_preserves_other_weeks_and_public_privacy(self):
        import server
        team='1.l.2.t.1';other='1.l.2.t.2'
        players=[{'key':'1.p.1','name':'P','team':'KC','points':'12','stats':[]}]
        match={'week':'1','status':'preevent','teams':[{'key':team,'name':'Private name','mine':True,'players':[]},{'key':other,'name':'Private opponent','mine':False,'players':[]}]}
        import copy
        s={'league':{'key':'1.l.2','name':'Secret'},'team':{'key':team},'teams':[{'key':team},{'key':other}],'standings':[],'availablePlayers':[],'matchups':{'1':copy.deepcopy(match),'2':{'teams':[],'status':'untouched'}},'teamMatchups':{team:{'1':copy.deepcopy(match)},other:{'1':copy.deepcopy(match)}},'weeklyStats':{},'seasonStats':{},'nflSchedule':{}}
        board=server.xml(('<f><matchup><status>midevent</status><teams>'+''.join(f'<team><team_key>{k}</team_key><team_points><total>42</total></team_points></team>' for k in (team,other))+'</teams></matchup></f>').encode())
        with tempfile.TemporaryDirectory() as t,patch.object(refresh,'DATA',Path(t)/'private'),patch.object(refresh,'PUBLIC',Path(t)/'public'),patch.object(server.yahoo,'get',return_value=board) as get,patch.object(server.yahoo,'roster',return_value=players) as roster:
            refresh.refresh_scores(s,['1'])
            self.assertEqual(get.call_count,1);self.assertEqual(roster.call_count,2)
            self.assertEqual(s['matchups']['1']['teams'][0]['points'],'42')
            self.assertEqual(s['matchups']['2']['status'],'untouched')
            public=json.loads((Path(t)/'public/public.json').read_text())
            self.assertNotIn('Private name',json.dumps(public));self.assertNotIn('teamMatchups',public)

    def test_full_refresh_interval_persists_and_validates(self):
        with tempfile.TemporaryDirectory() as t:
            a=Accounts(Path(t)/'db');self.assertEqual(a.refresh_interval(),3)
            a.refresh_interval(6,'admin');self.assertEqual(Accounts(Path(t)/'db').refresh_interval(),6)
            for bad in (0,25,True,'3'):
                with self.assertRaises(ValueError): a.refresh_interval(bad)
        self.assertFalse(refresh.full_refresh_due(100,3,10899))
        self.assertTrue(refresh.full_refresh_due(100,3,10900))

    def test_persistent_sessions_idle_absolute_and_logout(self):
        with tempfile.TemporaryDirectory() as t:
            a=Accounts(Path(t)/'db');a.bootstrap('admin','test-password-long-123')
            with patch('accounts.time.time',return_value=1000000): token=a.login('admin','test-password-long-123')
            with patch('accounts.time.time',return_value=1000000+6*86400): self.assertIsNotNone(a.session(token))
            with patch('accounts.time.time',return_value=1000000+12*86400): self.assertIsNotNone(a.session(token))
            with patch('accounts.time.time',return_value=1000000+20*86400): self.assertIsNone(a.session(token))
            with patch('accounts.time.time',return_value=1000000): token=a.login('admin','test-password-long-123')
            with patch('accounts.time.time',return_value=1000000+30*86400+1): self.assertIsNone(a.session(token))
            token=a.login('admin','test-password-long-123');a.logout(token);self.assertIsNone(a.session(token))
