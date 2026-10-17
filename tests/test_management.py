import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from accounts import Accounts
from management import dashboard

class ManagementTests(unittest.TestCase):
    def test_completed_weeks_only_and_missing_not_zero(self):
        p={'key':'p','name':'Player','slot':'QB','position':'QB','bye':'4','status':'Q'}
        weeks={str(w):{'status':'postevent' if w<4 else 'preevent','teams':[{'key':'t','name':'Team','players':[p]}]} for w in range(1,6)}
        s={'team':{'key':'t'},'matchups':weeks,'teamMatchups':{'t':weeks},'updated':'now','weeklyStats':{'1':{'p':{'points':'10'}},'2':{},'3':{'p':{'points':'20'}},'4':{'p':{'points':'0'}}},'rosterSlots':[{'position':'RB','count':'2'}]}
        r=dashboard(s,'t','4')
        self.assertEqual(r['trends'][0]['recent'],15)
        self.assertEqual(r['trends'][0]['samples'],2)
        self.assertEqual(r['recentWeeks'],[1,2,3])
        self.assertEqual(len(r['alerts']),3)
        with self.assertRaises(ValueError): dashboard(s,'t',None)

    def test_queue_is_atomic_capped_and_recoverable(self):
        with tempfile.TemporaryDirectory() as t:
            a=Accounts(Path(t)/'db')
            with patch('accounts.time.time',return_value=100000):
                a.request_refresh('admin')
                with self.assertRaises(ValueError): a.request_refresh('admin')
                jid=a.claim_refresh();self.assertIsNone(a.claim_refresh());a.finish_refresh(jid,True)
                with self.assertRaises(ValueError): a.request_refresh('admin')
            for now in (101000,102000,103000):
                with patch('accounts.time.time',return_value=now):
                    a.request_refresh('admin');a.finish_refresh(a.claim_refresh(),True)
            with patch('accounts.time.time',return_value=104000):
                with self.assertRaises(ValueError): a.request_refresh('admin')

    def test_notes_are_user_scoped(self):
        with tempfile.TemporaryDirectory() as t:
            a=Accounts(Path(t)/'db');a.add_note(1,'watch',{'player':'p'})
            nid=a.notes(1)[0]['id'];self.assertEqual(a.notes(2),[])
            a.delete_note(2,nid);self.assertEqual(len(a.notes(1)),1)
            a.delete_note(1,nid);self.assertEqual(a.notes(1),[])

    def test_position_metrics_distinguish_quarterback_and_defense(self):
        players=[{'key':k,'name':k,'position':pos,'slot':pos} for k,pos in [('q','QB'),('k','K'),('d','DEF')]]
        weeks={'1':{'status':'postevent','teams':[{'key':'t','name':'T','players':players}]},'2':{'status':'preevent','teams':[{'key':'t','name':'T','players':players}]}}
        labels={'4':'Pass Yds','6':'Int','32':'Sack','33':'Int','29':'PAT Made','78':'Targets'}
        s={'team':{'key':'t'},'matchups':weeks,'updated':'now','statLabels':labels,'weeklyStats':{'1':{p['key']:{'points':'0','stats':[{'id':sid,'value':'1'} for sid in labels]} for p in players}}}
        r=dashboard(s,'t',2)['trends']
        self.assertEqual(set(r[0]['volume']),{'Pass Yds','Int'})
        self.assertEqual(set(r[1]['volume']),{'PAT Made'})
        self.assertEqual(set(r[2]['volume']),{'Sack','Int'})
        self.assertEqual(r[0]['samples'],1)
