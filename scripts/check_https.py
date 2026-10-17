"""Local smoke check with certificate validation; never calls Azure or Yahoo."""
import http.cookiejar
import json
from pathlib import Path
import socket
import ssl
import urllib.request
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
values={line.split('=',1)[0]:line.split('=',1)[1] for line in (root/'.env').read_text().splitlines() if '=' in line and not line.startswith('#')}
context=ssl.create_default_context(cafile=str(root/'data/local-ca.crt'))
original=socket.getaddrinfo
def resolve(host,port,*args,**kwargs):
    if host in ('fantasy.localhost','fantasy.localhost'):
        host='127.0.0.1'
    return original(host,port,*args,**kwargs)
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def get(url):
    with client.open(url,timeout=15) as response:
        return json.load(response)
def post(path,body,csrf=None):
    origin='https://fantasy.localhost:9443'
    headers={'Origin':origin,'Content-Type':'application/json'}
    if csrf:headers['X-CSRF-Token']=csrf
    with client.open(urllib.request.Request(origin+'/private'+path,json.dumps(body).encode(),headers),timeout=15) as response:
        return json.load(response)
with patch('socket.getaddrinfo',side_effect=resolve):
    print('HTTPS certificate validation: enabled; hostname verification: enabled')
    print('Public config:',get('https://fantasy.localhost:9443/api/config'))
    post('/api/auth/login',{'username':values['ADMIN_USERNAME'],'password':values['ADMIN_PASSWORD']})
    state=get('https://fantasy.localhost:9443/private/api/auth/session')
    print('Private admin sign-in:',bool(state['authenticated'] and state['user']['admin']))
    print('User management accessible:',isinstance(get('https://fantasy.localhost:9443/private/api/admin/users'),list))
    import urllib.parse
    league=get('https://fantasy.localhost:9443/private/api/leagues')[0]
    teams=get('https://fantasy.localhost:9443/private/api/teams?league='+league['key'])
    q=urllib.parse.urlencode({'league':league['key'],'team':teams[0]['key'],'week':league['week']})
    tools=get('https://fantasy.localhost:9443/private/api/manage?'+q)
    print('Private league tools loaded:',all(k in tools for k in ('trends','byes','notes','matchups','available','standings')))
    print('Cached standings / available players:',len(tools['standings']),len(tools['available']))
    post('/api/auth/logout',{},state['user']['csrf'])
    print('Logout revoked session:',not get('https://fantasy.localhost:9443/private/api/auth/session')['authenticated'])
    leagues=get('https://fantasy.localhost:9443/api/leagues')
    print('Public daily snapshot loaded:',len(leagues)==1)
