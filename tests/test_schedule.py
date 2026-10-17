import unittest
from schedule import parse_schedule,enrich

class ScheduleTests(unittest.TestCase):
    def test_season_week_timezone_alias_and_neutral(self):
        rows='season,game_type,week,gameday,gametime,home_team,away_team,location\n2026,REG,6,2026-10-18,16:25,KC,JAX,Home\n2026,REG,10,2026-11-15,13:00,WAS,LA,Neutral\n2025,REG,6,2025-10-18,13:00,KC,BUF,Home\n2026,WC,19,2027-01-10,13:00,KC,BUF,Home\n'
        s=parse_schedule(rows,2026)
        self.assertEqual(s['6']['KC']['kickoff'],'2026-10-18T20:25:00+00:00')
        self.assertEqual(s['10']['WAS']['kickoff'],'2026-11-15T18:00:00+00:00')
        self.assertEqual(s['10']['LA']['location'],'neutral')
        self.assertNotIn('19',s)
        players=[{'team':'Jac','bye':'9'},{'team':'KC','bye':'6'},{'team':'unknown'}]
        snap={'matchups':{'6':{'teams':[{'players':players}]}}}
        enrich(snap,s)
        self.assertEqual(players[0]['nflGame']['opponent'],'KC')
        self.assertEqual(players[0]['nflGame']['location'],'away')
        self.assertTrue(players[1]['nflGame']['bye'])
        self.assertIsNone(players[2]['nflGame'])

    def test_tbd_does_not_invent_time(self):
        s=parse_schedule('season,game_type,week,gameday,gametime,home_team,away_team\n2026,REG,18,2027-01-10,NA,KC,BUF\n',2026)
        self.assertIsNone(s['18']['KC']['kickoff'])
        self.assertEqual(s['18']['KC']['opponent'],'BUF')
