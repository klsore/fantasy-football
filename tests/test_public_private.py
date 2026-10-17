import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from webapp import create_app
from accounts import Accounts


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'ADMIN_USERNAME':'admin','ADMIN_PASSWORD':'test-admin-password-12345',
            'PUBLIC_HOST':'public.test','PRIVATE_HOST':'private.test','SNAPSHOT_DIR':str(self.root)})
        self.env.start();self.addCleanup(self.env.stop)
        p={'points':'2.50','stats':[{'id':'4','value':'100'}]}
        self.snapshot={'league':{'key':'1.l.2','name':'Public','season':'2026','week':'1','startWeek':'1','endWeek':'1'},
            'team':{'key':'1.l.2.t.3','name':'Featured','mine':True},'updated':'2026-10-15T00:00:00Z',
            'statLabels':{'4':'Pass Yds'},'seasonStats':{'1.p.4':p},'weeklyStats':{'1':{'1.p.4':p}},
            'matchups':{'1':{'week':'1','status':'postevent','teams':[],'statLabels':{'4':'Pass Yds'}}}}
        (self.root/'public.json').write_text(json.dumps(self.snapshot))
        (self.root/'private-snapshot.json').write_text(json.dumps(self.snapshot))

    def private(self):
        self.app=create_app('private',self.root)
        return self.app.test_client()

    def login(self,client,user='admin',password='test-admin-password-12345'):
        response=client.post('/api/auth/login',json={'username':user,'password':password},base_url='https://private.test',headers={'Origin':'https://private.test'})
        self.assertEqual(response.status_code,200)
        state=client.get('/api/auth/session',base_url='https://private.test').json
        return {'Origin':'https://private.test','X-CSRF-Token':state['user']['csrf']}

    def test_public_is_cached_only_no_network_no_ai(self):
        app=create_app('public',self.root);client=app.test_client()
        with patch('urllib.request.urlopen') as network:
            self.assertEqual(client.get('/api/leagues',base_url='https://public.test').status_code,200)
            self.assertEqual(client.get('/api/matchup?league=1.l.2&team=1.l.2.t.3&week=1',base_url='https://public.test').status_code,200)
            self.assertEqual(client.get('/api/player-stats?league=1.l.2&player=1.p.4&mode=season',base_url='https://public.test').json['points'],'2.50')
            self.assertEqual(client.post('/api/analytics',json={},base_url='https://public.test').status_code,405)
            self.assertEqual(client.get('/api/admin/users',base_url='https://public.test').status_code,404)
            network.assert_not_called()

    def test_missing_cache_never_fetches(self):
        (self.root/'public.json').unlink()
        with patch('urllib.request.urlopen') as network:
            c=create_app('public',self.root).test_client()
            self.assertEqual(c.get('/api/leagues',base_url='https://public.test').status_code,503)
            network.assert_not_called()

    def test_briefings_persist_across_restart_and_are_private(self):
        c=self.private();headers=self.login(c)
        self.snapshot['matchups']['1']['teams']=[{'name':'Selected','mine':True,'points':'1','projected':'2','players':[]}]
        (self.root/'private-snapshot.json').write_text(json.dumps(self.snapshot))
        report={'text':'## Matchup verdict\nFirst report','usage':{'input_tokens':12,'output_tokens':34},'incomplete':False}
        with patch('intelligence.Intelligence.analyze',return_value=report) as azure:
            response=c.post('/api/analytics',json={'league':'1.l.2','team':'1.l.2.t.3','week':'1'},base_url='https://private.test',headers=headers)
            self.assertEqual(response.status_code,200)
            rid=response.json['saved']['id']
            self.assertEqual(azure.call_count,1)
        # Recreate the application; history is independent of the snapshot file.
        app=create_app('private',self.root);reader=app.test_client()
        self.login(reader)
        (self.root/'private-snapshot.json').unlink()
        with patch('urllib.request.urlopen') as network, patch('intelligence.Intelligence.analyze') as azure:
            saved=reader.get('/api/briefings/'+str(rid),base_url='https://private.test')
            self.assertEqual(saved.status_code,200)
            self.assertEqual(saved.json['text'],report['text'])
            self.assertEqual(saved.json['usage'],report['usage'])
            self.assertEqual(saved.json['saved']['creator'],'admin')
            listing=reader.get('/api/briefings?league=1.l.2&team=1.l.2.t.3&week=1',base_url='https://private.test').json
            self.assertEqual(listing['items'][0]['id'],rid)
            self.assertEqual(app.test_client().get('/api/briefings/'+str(rid),base_url='https://private.test').status_code,401)
            public=create_app('public',self.root).test_client()
            self.assertEqual(public.get('/api/briefings/'+str(rid),base_url='https://public.test').status_code,404)
            network.assert_not_called();azure.assert_not_called()

    def test_history_pagination_keeps_every_version(self):
        c=self.private();self.login(c)
        for i in range(23):
            self.app.accounts.save_briefing('1.l.2','1.l.2.t.3','1','2026','2026-10-14T00:00:00Z','admin','test','hash',['A','B'],{'text':str(i)})
        path='/api/briefings?league=1.l.2&team=1.l.2.t.3&week=1'
        first=c.get(path,base_url='https://private.test').json
        self.assertEqual(len(first['items']),20)
        second=c.get(path+'&before='+str(first['nextBefore']),base_url='https://private.test').json
        self.assertEqual(len(second['items']),3)
        self.assertIsNone(second['nextBefore'])
        self.assertEqual(len({r['id'] for r in first['items']+second['items']}),23)

    def test_private_can_browse_other_teams_but_public_cannot(self):
        other={'key':'1.l.2.t.8','name':'Other team','mine':False}
        s=dict(self.snapshot)
        s['teams']=[s['team'],other]
        s['teamMatchups']={other['key']:{'1':{'week':'1','status':'postevent','teams':[], 'statLabels':s['statLabels']}}}
        (self.root/'private-snapshot.json').write_text(json.dumps(s))
        private=self.private();self.login(private)
        public=create_app('public',self.root).test_client()
        with patch('urllib.request.urlopen') as network:
            self.assertEqual(len(private.get('/api/teams?league=1.l.2',base_url='https://private.test').json),2)
            path='/api/matchup?league=1.l.2&team=1.l.2.t.8&week=1'
            self.assertEqual(private.get(path,base_url='https://private.test').status_code,200)
            self.assertEqual(len(public.get('/api/teams?league=1.l.2',base_url='https://public.test').json),1)
            self.assertEqual(public.get(path,base_url='https://public.test').status_code,404)
            self.assertEqual(private.get(path.replace('.t.8','.t.999'),base_url='https://private.test').status_code,404)
            network.assert_not_called()

    def test_private_login_secure_cookie_csrf_and_logout(self):
        c=self.private()
        self.assertEqual(c.get('/api/leagues',base_url='https://private.test').status_code,401)
        result=c.post('/api/auth/login',json={'username':'admin','password':'test-admin-password-12345'},base_url='https://private.test',headers={'Origin':'https://private.test'})
        self.assertEqual(result.status_code,200)
        cookie=result.headers['Set-Cookie']
        for marker in ('__Host-matchup','Secure','HttpOnly','SameSite=Strict','Path=/'):
            self.assertIn(marker,cookie)
        csrf=c.get('/api/auth/session',base_url='https://private.test').json['user']['csrf']
        self.assertEqual(c.post('/api/auth/logout',json={},base_url='https://private.test',headers={'Origin':'https://private.test'}).status_code,403)
        self.assertEqual(c.post('/api/auth/logout',json={},base_url='https://private.test',headers={'Origin':'https://evil.test','X-CSRF-Token':csrf}).status_code,403)
        self.assertEqual(c.post('/api/auth/logout',json={},base_url='https://private.test',headers={'Origin':'https://private.test','X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(c.get('/api/leagues',base_url='https://private.test').status_code,401)

    def test_user_roles_deletion_revokes_sessions_and_passwords_hashed(self):
        c=self.private();headers=self.login(c)
        self.assertEqual(c.post('/api/admin/users',json={'username':'viewer','password':'viewer-password-12345'},base_url='https://private.test',headers=headers).status_code,201)
        viewer=self.app.test_client();viewer_headers=self.login(viewer,'viewer','viewer-password-12345')
        self.assertEqual(viewer.get('/api/admin/users',base_url='https://private.test').status_code,403)
        self.assertEqual(viewer.post('/api/analytics',json={},base_url='https://private.test',headers=viewer_headers).status_code,403)
        users=c.get('/api/admin/users',base_url='https://private.test').json
        uid=next(u['id'] for u in users if u['username']=='viewer')
        self.assertEqual(c.delete('/api/admin/users/'+str(uid),json={},base_url='https://private.test',headers=headers).status_code,200)
        self.assertEqual(viewer.get('/api/leagues',base_url='https://private.test').status_code,401)
        self.assertEqual(c.delete('/api/admin/users/1',json={},base_url='https://private.test',headers=headers).status_code,400)
        with self.app.accounts.db() as db:
            stored=db.execute('SELECT password FROM users').fetchone()[0]
        self.assertNotEqual(stored,'test-admin-password-12345')
        self.assertTrue(Accounts.verify('test-admin-password-12345',stored))

    def test_tls_host_allowlist_security_headers_and_invalid_queries(self):
        c=create_app('public',self.root).test_client()
        self.assertEqual(c.get('/',base_url='http://public.test').status_code,400)
        self.assertEqual(c.get('/',base_url='https://evil.test').status_code,400)
        result=c.get('/',base_url='https://public.test')
        self.assertEqual(result.status_code,200)
        self.assertIn('frame-ancestors',result.headers['Content-Security-Policy'])
        self.assertIn('max-age',result.headers['Strict-Transport-Security'])
        result.close()
        self.assertEqual(c.get('/.env',base_url='https://public.test').status_code,404)
        self.assertEqual(c.get('/api/player-stats?league=1.l.2&player=other&mode=season',base_url='https://public.test').status_code,404)

    def test_persistent_login_rate_limit(self):
        c=self.private()
        for _ in range(5):
            self.assertEqual(c.post('/api/auth/login',json={'username':'admin','password':'wrong'},base_url='https://private.test',headers={'Origin':'https://private.test'}).status_code,401)
        c=create_app('private',self.root).test_client()
        self.assertEqual(c.post('/api/auth/login',json={'username':'admin','password':'test-admin-password-12345'},base_url='https://private.test',headers={'Origin':'https://private.test'}).status_code,429)

    def test_password_change_invalidates_existing_sessions(self):
        c=self.private();headers=self.login(c)
        self.assertEqual(c.post('/api/auth/password',json={'current':'test-admin-password-12345','password':'new-admin-password-12345'},base_url='https://private.test',headers=headers).status_code,200)
        self.assertEqual(c.get('/api/leagues',base_url='https://private.test').status_code,401)
        self.login(c,password='new-admin-password-12345')

    def test_management_endpoints_private_no_network_and_admin_refresh(self):
        c=self.private()
        path='/api/manage?league=1.l.2&team=1.l.2.t.3&week=1'
        self.assertEqual(c.get(path,base_url='https://private.test').status_code,401)
        headers=self.login(c)
        with patch('urllib.request.urlopen') as network, patch('intelligence.Intelligence.analyze') as azure:
            self.assertEqual(c.get(path,base_url='https://private.test').status_code,200)
            self.assertEqual(c.post('/api/admin/refresh',json={},headers=headers,base_url='https://private.test').status_code,202)
            self.assertEqual(c.post('/api/admin/refresh',json={},headers=headers,base_url='https://private.test').status_code,429)
            network.assert_not_called();azure.assert_not_called()
        self.app.accounts.add('reader','reader-password-12345',False,'admin')
        reader=self.app.test_client();rh=self.login(reader,'reader','reader-password-12345')
        self.assertEqual(reader.post('/api/admin/refresh',json={},headers=rh,base_url='https://private.test').status_code,403)
        public=create_app('public',self.root).test_client()
        self.assertEqual(public.get(path,base_url='https://public.test').status_code,404)

    def test_briefing_diff_uses_previous_same_matchup(self):
        c=self.private();self.login(c)
        a=self.app.accounts
        a.save_briefing('1.l.2','1.l.2.t.3',1,2026,'old','admin','test','hash',[],{'text':'old verdict'})
        a.save_briefing('1.l.2','1.l.2.t.8',1,2026,'old','admin','test','hash',[],{'text':'unrelated'})
        rid=a.save_briefing('1.l.2','1.l.2.t.3',1,2026,'new','admin','test','hash',[],{'text':'new verdict'})
        result=c.get(f'/api/briefings/{rid}/compare',base_url='https://private.test').json
        self.assertIn('-old verdict',result['changes']);self.assertIn('+new verdict',result['changes'])

    def test_live_toggle_requires_admin_and_csrf(self):
        c=self.private();h=self.login(c)
        self.assertFalse(c.get('/api/admin/live',base_url='https://private.test').json['enabled'])
        self.assertEqual(c.post('/api/admin/live',json={'enabled':True},base_url='https://private.test',headers={'Origin':'https://private.test'}).status_code,403)
        self.assertTrue(c.post('/api/admin/live',json={'enabled':True},base_url='https://private.test',headers=h).json['enabled'])
        self.assertEqual(c.post('/api/admin/live',json={'enabled':'false'},base_url='https://private.test',headers=h).status_code,400)
        self.app.accounts.add('viewer','viewer-password-123456',False,'admin');r=self.app.test_client();rh=self.login(r,'viewer','viewer-password-123456')
        self.assertEqual(r.post('/api/admin/live',json={'enabled':False},base_url='https://private.test',headers=rh).status_code,403)

    def test_refresh_interval_admin_only(self):
        c=self.private();h=self.login(c)
        path='/api/admin/refresh-settings'
        self.assertEqual(c.get(path,base_url='https://private.test').json['hours'],3)
        self.assertEqual(c.post(path,json={'hours':6},headers=h,base_url='https://private.test').json['hours'],6)
        self.assertEqual(c.post(path,json={'hours':0},headers=h,base_url='https://private.test').status_code,400)
        self.app.accounts.add('reader','reader-password-123456',False,'admin');r=self.app.test_client();rh=self.login(r,'reader','reader-password-123456')
        self.assertEqual(r.post(path,json={'hours':1},headers=rh,base_url='https://private.test').status_code,403)

    def test_team_assignment_and_private_reports_between_users(self):
        c=self.private();h=self.login(c);a=self.app.accounts
        a.add('reader','reader-password-123456',False,'admin')
        uid=next(u['id'] for u in a.users() if u['username']=='reader')
        path=f'/api/admin/users/{uid}/team'
        self.assertEqual(c.put(path,json={'team':'bad'},headers=h,base_url='https://private.test').status_code,400)
        self.assertEqual(c.put(path,json={'team':'1.l.2.t.3'},headers=h,base_url='https://private.test').status_code,200)
        reader=self.app.test_client();rh=self.login(reader,'reader','reader-password-123456')
        self.assertEqual(reader.get('/api/auth/session',base_url='https://private.test').json['user']['team_key'],'1.l.2.t.3')
        self.assertEqual(reader.put(path,json={'team':''},headers=rh,base_url='https://private.test').status_code,403)
        rid=a.save_briefing('1.l.2','1.l.2.t.3',1,2026,'now','reader','test','hash',[],{'text':'private reader'})
        aid=a.save_briefing('1.l.2','1.l.2.t.3',1,2026,'now','admin','test','hash',[],{'text':'private admin'})
        for client,blocked in ((c,rid),(reader,aid)):
            self.assertEqual(client.get(f'/api/briefings/{blocked}',base_url='https://private.test').status_code,404)
            self.assertEqual(client.get(f'/api/briefings/{blocked}/compare',base_url='https://private.test').status_code,404)
        listing=reader.get('/api/briefings?league=1.l.2&team=1.l.2.t.3&week=1',base_url='https://private.test').json
        self.assertEqual([r['id'] for r in listing['items']],[rid])
        a.delete(uid,1,'admin');a.add('reader','reader-password-123456',False,'admin')
        self.assertIsNone(a.briefing(rid))

    def test_admin_ai_limits_persist_and_are_private(self):
        c=self.private();h=self.login(c);path='/api/admin/ai-settings'
        values={'monthlyLimit':50,'maxOutputTokens':2048}
        self.assertEqual(c.post(path,json=values,headers=h,base_url='https://private.test').json,values)
        state=c.get('/api/ai/session',base_url='https://private.test').json
        self.assertEqual(state['monthlyLimit'],50);self.assertEqual(state['maxOutputTokens'],2048)
        for bad in ({'monthlyLimit':True,'maxOutputTokens':1024},{'monthlyLimit':1,'maxOutputTokens':0},{'monthlyLimit':1}):
            self.assertEqual(c.post(path,json=bad,headers=h,base_url='https://private.test').status_code,400)
        self.assertEqual(Accounts(self.root/'accounts.sqlite3').ai_settings(),values)
        self.app.accounts.add('reader','reader-password-123456',False,'admin');r=self.app.test_client();rh=self.login(r,'reader','reader-password-123456')
        self.assertEqual(r.post(path,json=values,headers=rh,base_url='https://private.test').status_code,403)

    def test_background_reports_for_assigned_user_are_saved_privately(self):
        import time
        c=self.private();h=self.login(c);a=self.app.accounts
        a.add('member','test-password-member-123',False,'admin')
        uid=next(u['id'] for u in a.users() if u['username']=='member')
        a.assign_team(uid,'1.l.2.t.3','admin')
        reader=self.app.test_client();rh=self.login(reader,'member','test-password-member-123')
        self.snapshot['matchups']['1']['teams']=[{'key':'1.l.2.t.3','name':'Example team','mine':True,'points':'1','projected':'2','players':[]}]
        (self.root/'private-snapshot.json').write_text(json.dumps(self.snapshot))
        body={'league':'1.l.2','team':'1.l.2.t.3','week':'1'}
        with patch('intelligence.Intelligence.analyze',return_value={'text':'Assigned user report','usage':{}}) as azure:
            start=reader.post('/api/analytics/jobs',json=body,headers=rh,base_url='https://private.test')
            self.assertEqual(start.status_code,202)
            jid=start.json['id'];path='/api/analytics/jobs/'+jid
            for _ in range(100):
                state=reader.get(path,base_url='https://private.test').json
                if state['status']!='running': break
                time.sleep(.01)
            self.assertEqual(state['status'],'complete')
            self.assertEqual(state['result']['saved']['creator'],'member')
            self.assertEqual(state['result']['text'],'Assigned user report')
            azure.assert_called_once()
        self.assertEqual(c.get(path,base_url='https://private.test').status_code,404)
        self.assertEqual(c.get('/api/briefings/'+str(state['result']['saved']['id']),base_url='https://private.test').status_code,404)

    def test_background_azure_failure_does_not_save_or_retry(self):
        import time
        from intelligence import IntelligenceError
        c=self.private();h=self.login(c)
        self.snapshot['matchups']['1']['teams']=[{'name':'A','mine':True,'points':'1','projected':'2','players':[]}]
        (self.root/'private-snapshot.json').write_text(json.dumps(self.snapshot))
        with patch('intelligence.Intelligence.analyze',side_effect=IntelligenceError('Azure returned HTTP 503',502)) as azure:
            r=c.post('/api/analytics/jobs',json={'league':'1.l.2','team':'1.l.2.t.3','week':'1'},headers=h,base_url='https://private.test')
            self.assertEqual(r.status_code,202)
            for _ in range(100):
                state=c.get('/api/analytics/jobs/'+r.json['id'],base_url='https://private.test').json
                if state['status']!='running': break
                time.sleep(.01)
            self.assertEqual(state['status'],'failed')
            self.assertIn('503',state['result']['error']);azure.assert_called_once()
        self.assertEqual(self.app.accounts.briefings('1.l.2','1.l.2.t.3','1'),[])
