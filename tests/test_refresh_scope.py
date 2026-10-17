import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import refresh
import server


class RefreshScopeTests(unittest.TestCase):
    def test_private_league_cache_is_not_published(self):
        league={'key':'1.l.2','name':'Private league','startWeek':'1','endWeek':'1'}
        teams=[{'key':f'1.l.2.t.{i}','name':f'Private team {i}','mine':i==1,'manager':'Private manager'} for i in range(1,5)]
        def matchup(league,team,week):
            pair=[1,2] if team in ('1.l.2.t.1','1.l.2.t.2') else [3,4]
            return {'week':week,'statLabels':{},'teams':[{'key':f'1.l.2.t.{i}','mine':team==f'1.l.2.t.{i}', 'name':f'Private team {i}',
                'players':[{'key':f'1.p.{i}','points':'1','stats':[]}]} for i in pair]}
        root=server.xml(('<f>'+''.join(f'<player><player_key>1.p.{i}</player_key></player>' for i in range(1,5))+'</f>').encode())
        with tempfile.TemporaryDirectory() as folder, patch.dict(refresh.os.environ,{'PUBLIC_LEAGUE_KEY':'1.l.2','PUBLIC_TEAM_KEY':'1.l.2.t.1'}), patch.object(refresh,'DATA',Path(folder)/'private'), patch.object(refresh,'PUBLIC',Path(folder)/'public'), patch.object(server.yahoo,'leagues',return_value=[league]), patch.object(server.yahoo,'teams',return_value=teams), patch.object(server.yahoo,'matchup',side_effect=matchup), patch.object(server.yahoo,'get',return_value=root), patch('schedule.fetch_schedule',return_value={}):
            refresh.refresh()
            private=json.loads((Path(folder)/'private/private-snapshot.json').read_text())
            public=json.loads((Path(folder)/'public/public.json').read_text())
        self.assertEqual(private['teamMatchups']['1.l.2.t.1']['1']['teams'][0]['manager'],'Private manager')
        self.assertEqual(len(private['teams']),4)
        self.assertEqual(len(private['teamMatchups']),4)
        self.assertNotIn('teams',public)
        self.assertNotIn('teamMatchups',public)
        self.assertEqual(set(public['seasonStats']),{'1.p.1','1.p.2'})
        self.assertEqual(set(public['weeklyStats']['1']),{'1.p.1','1.p.2'})
        self.assertNotIn('Private team',json.dumps(public))
        self.assertNotIn('Private manager',json.dumps(public))

    def test_backfill_unrostered_players_batches_and_preserves_zero(self):
        from unittest.mock import Mock
        y=Mock()
        y.get.return_value=server.xml(b'<f><player><player_key>1.p.missing</player_key><player_points><total>12.5</total></player_points></player></f>')
        y.player.side_effect=server.Yahoo().player
        s={'teamMatchups':{'t':{'1':{'status':'postevent'},'2':{'status':'preevent'}}},'weeklyStats':{'1':{'zero':{'points':'0','stats':[{'id':'4','value':'0'}]}},'2':{}}}
        refresh.backfill_weekly(y,'1.l.2',s,['1.p.missing','zero'])
        y.get.assert_called_once_with('league/1.l.2/players;player_keys=1.p.missing/stats;type=week;week=1')
        self.assertEqual(s['weeklyStats']['1']['1.p.missing']['points'],'12.5')
        self.assertEqual(s['weeklyStats']['1']['zero']['points'],'0')
        self.assertEqual(s['weeklyStats']['2'],{})
