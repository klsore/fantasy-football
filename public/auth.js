const apiPrefix = location.pathname.startsWith('/private') ? '/private' : '';
let siteSession = null;
const siteReady = (async () => {
  const config = await fetch(apiPrefix + '/api/config').then(r => r.json());
  if (config.mode === 'public') {
    document.querySelector('.tag').textContent = 'PUBLIC | DAILY SNAPSHOT';
    const link = document.createElement('a'); link.href = '/login'; link.textContent = 'Sign in'; link.className = 'sign-in-link'; document.querySelector('header').append(link);
    return config;
  }
  siteSession = await fetch(apiPrefix + '/api/auth/session').then(r => r.json());
  if (!siteSession.authenticated) { location.replace('/login'); throw Error('Sign in required'); }
  document.querySelector('.tag').textContent = 'PRIVATE | ' + siteSession.user.username;
  const controls = document.createElement('div'); controls.className = 'account-controls';
  const logout = document.createElement('button'); logout.textContent = 'Sign out';
  logout.onclick = async () => { await secureAPI('/api/auth/logout',{}); location.assign('/'); };
  const password = document.createElement('button'); password.textContent = 'Change password';
  password.onclick = () => document.getElementById('password-dialog').showModal();
  controls.append(password,logout); document.querySelector('header').append(controls);
  if (siteSession.user.admin) {
    const users = document.createElement('button'); users.textContent = 'Manage users'; controls.prepend(users);
    users.onclick = async () => { document.getElementById('users-dialog').showModal(); await listUsers(); };
  }
  return config;
})();
async function secureAPI(url, body, method='POST') {
  const response = await fetch(apiPrefix + url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':siteSession?.user.csrf || ''},body:JSON.stringify(body)});
  const data = await response.json(); if (!response.ok) throw Error(data.error || 'Request failed'); return data;
}
async function listUsers() {
  const response = await fetch(apiPrefix + '/api/admin/users'), data = await response.json();
  if (!response.ok) { document.getElementById('user-message').textContent = data.error; return; }
  const cachedLeagues=await fetch(apiPrefix+'/api/leagues').then(r=>r.json());
  const teams=cachedLeagues.length ? await fetch(apiPrefix+'/api/teams?league='+encodeURIComponent(cachedLeagues[0].key)).then(r=>r.json()) : [];
  const box=document.getElementById('user-list'); box.replaceChildren();
  for (const user of data) {
    const row=document.createElement('div'); row.className='user-row';
    const name=document.createElement('span'); name.textContent=user.username+(user.admin?' (admin)':' (viewer)');
    const button=document.createElement('button'); button.textContent='Delete'; button.disabled=user.id===siteSession.user.id;
    button.onclick=async () => { if (!confirm('Delete user '+user.username+' and revoke their sessions?')) return; try { await secureAPI('/api/admin/users/'+user.id,{},'DELETE'); await listUsers(); } catch(e) {document.getElementById('user-message').textContent=e.message;} };
    const label=document.createElement('label');label.textContent='ASSIGNED TEAM';
    const select=document.createElement('select');select.setAttribute('aria-label','Assigned team for '+user.username);
    for(const team of [{key:'',name:'Not assigned'},...teams]){const option=document.createElement('option');option.value=team.key;option.textContent=team.name;select.append(option);}
    select.value=user.team_key||'';label.append(select);
    select.onchange=async()=>{select.disabled=true;try{await secureAPI('/api/admin/users/'+user.id+'/team',{team:select.value},'PUT');document.getElementById('user-message').textContent='Team assignment saved for '+user.username+'.';if(user.id===siteSession.user.id)siteSession.user.team_key=select.value;}catch(e){document.getElementById('user-message').textContent=e.message;select.value=user.team_key||'';}finally{select.disabled=false;}};
    row.append(name,label,button); box.append(row);
  }
}
document.getElementById('user-form').onsubmit=async event => {
  event.preventDefault();const form=event.currentTarget;
  try { await secureAPI('/api/admin/users',{username:form.elements.username.value,password:form.elements.password.value,admin:form.elements.admin.checked});form.reset();await listUsers();document.getElementById('user-message').textContent='User created.'; }
  catch(e) {document.getElementById('user-message').textContent=e.message;}
};
document.getElementById('password-form').onsubmit=async event => {
  event.preventDefault();const form=event.currentTarget;
  try {await secureAPI('/api/auth/password',{current:form.elements.current.value,password:form.elements.password.value});location.assign('/');}
  catch(e) {document.getElementById('password-message').textContent=e.message;}
};
document.querySelectorAll('[data-close]').forEach(button => button.onclick=()=>button.closest('dialog').close());
