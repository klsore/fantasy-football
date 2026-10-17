const $ = id => document.getElementById(id);
let leagues = [], current = null, requestId = 0, aiEnabled = false;
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
async function api(path, options) {
  if(options?.method && siteSession?.user) options.headers={...options.headers,'X-CSRF-Token':siteSession.user.csrf};
  const r=await fetch(apiPrefix+path,options);const text=await r.text();let data;
  try{data=JSON.parse(text);}catch{const e=Error(`The server or proxy returned HTTP ${r.status} instead of an API response. Check saved briefings before starting another report.`);e.status=r.status;throw e;}
  if(!r.ok){const e=Error(data.error||'Request failed');e.status=r.status;throw e;}return data;
}

function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; }
function options(select, items, label, key) { select.replaceChildren(...items.map(i => { const o = el('option', '', label(i)); o.value = key(i); return o; })); }
function params() { return { league: $('league').value, team: $('team').value, week: $('week').value }; }
function avatar(p) { const fallback = el('span', 'avatar', p.name.split(' ').map(s => s[0]).slice(0, 2).join('')); if (!p.photo || !p.photo.startsWith('https://')) return fallback; const img = el('img', 'avatar'); img.src = p.photo; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.onerror = () => img.replaceWith(fallback); return img; }
function score(n) { return n == null || n === '' ? '—' : Number.isFinite(Number(n)) ? Number(n).toFixed(2) : n; }
let statsRequest = 0;
function statCategory(id){
  const n=Number(id);
  if([4,5,6,7,58,59,60].includes(n))return 'Passing';
  if([8,9,10,61,62].includes(n))return 'Rushing';
  if([11,12,13,63,64,78].includes(n))return 'Receiving';
  if(n>=19&&n<=30)return 'Kicking';
  if([31,32,33,34,35,36,37,50,51,52,53,54,55,56,67,82].includes(n))return 'Defense';
  if([14,15,49].includes(n))return 'Returns';
  return 'Other scoring';
}
function showPlayer(p) {
  const league = $('league').value;
  const season = leagues.find(l => l.key === league)?.season || '';
  const box = $('player-content'); box.replaceChildren();
  const profile = el('div', 'profile'); profile.append(avatar(p));
  const info = el('div'); info.append(el('h2', '', p.name), el('div', 'player-meta', `${p.team} · ${p.position} · ${p.status || 'No status designation'}`));
  profile.append(info); box.append(profile);
  const controls = el('div', 'stats-controls');
  const modeLabel = el('label', '', 'TIMEFRAME'); const mode = el('select'); mode.setAttribute('aria-label', 'Statistics timeframe');
  options(mode, [{key:'season', name:`${season} season to date`},{key:'week', name:'Specific week'},{key:'range', name:'Range of weeks'}], i => i.name, i => i.key);
  modeLabel.append(mode);
  const startLabel = el('label', '', 'WEEK'); const start = el('select'); start.setAttribute('aria-label', 'Statistics start week');
  const endLabel = el('label', '', 'THROUGH WEEK'); const end = el('select'); end.setAttribute('aria-label', 'Statistics end week');
  const weeks = Array.from({length:18}, (_, i) => i + 1);
  options(start, weeks, n => n, n => n); options(end, weeks, n => n, n => n);
  start.value = current.week; end.value = current.week;
  startLabel.append(start); endLabel.append(end); controls.append(modeLabel, startLabel, endLabel); box.append(controls);
  const results = el('div', 'stats-results'); results.setAttribute('aria-live', 'polite'); box.append(results);
  box.append(el('p', 'stats-footnote', 'Season totals cover all games Yahoo reports this season, regardless of fantasy roster ownership. Weekly views use the selected season’s NFL weeks. Career/lifetime totals are not available in this integration.'));
  async function loadStats() {
    const id = ++statsRequest;
    startLabel.hidden = mode.value === 'season'; endLabel.hidden = mode.value !== 'range';
    results.replaceChildren(el('p', 'stats-notice', 'Loading Yahoo statistics…'));
    if (mode.value === 'range' && Number(start.value) > Number(end.value)) {
      results.replaceChildren(el('p', 'stats-notice', 'Choose an end week on or after the start week.')); return;
    }
    try {
      const data = await api('/api/player-stats?' + new URLSearchParams({league, player:p.key, mode:mode.value, start:start.value, end:end.value}));
      if (id !== statsRequest || !$('player-dialog').open) return;
      results.replaceChildren(el('div', 'eyebrow', `${season} · ${data.period.toUpperCase()}`));
      if (data.allZero) results.append(el('p', 'stats-notice', 'Yahoo reports zero statistics for this timeframe. This can mean games have not been played, the player did not play, or Yahoo has not posted stats yet. Try season totals or an earlier week.'));
      const summary=el('div','stats-summary');
      const heading=el('div');heading.append(el('span','eyebrow','FANTASY POINTS'),el('p','','Your league’s scoring · '+data.period));
      summary.append(heading,el('strong','',score(data.points)));results.append(summary);
      const grid=el('div','stat-groups');grid.setAttribute('aria-label','Player statistics');
      const groups=new Map();
      for(const stat of data.stats){
        const label=data.statLabels[stat.id]||`Stat ${stat.id}`;
        const category=statCategory(stat.id);
        if(!groups.has(category))groups.set(category,[]);
        groups.get(category).push([label,stat.value==null||stat.value===''?'Unavailable':stat.value]);
      }
      for(const [category,rows] of groups){
        const section=el('section','stat-group');section.append(el('h3','',category));const list=el('dl');
        for(const [label,value] of rows){const row=el('div','stat-line');row.append(el('dt','',label),el('dd','',value));list.append(row);}
        section.append(list);grid.append(section);
      }
      results.append(grid);
      if (!data.stats.length) results.append(el('p', 'stats-notice', 'No statistical breakdown is available from Yahoo for this timeframe.'));
      if (data.mode === 'range') results.append(el('p', 'stats-footnote', 'Range totals sum Yahoo’s weekly counting stats and fantasy points; unavailable values are not treated as zero. Rates and averages are omitted.' + (data.omitted.length ? ` Omitted: ${data.omitted.join(', ')}.` : '')));
    } catch (e) { if (id === statsRequest) results.replaceChildren(el('p', 'stats-notice', e.message)); }
  }
  mode.onchange = loadStats; start.onchange = loadStats; end.onchange = loadStats;
  $('player-dialog').showModal(); loadStats();
}
function gameLine(p) {
  const game=p.nflGame;
  if (game?.bye || String(p.bye)===String(current?.week)) return 'Bye week';
  if (!game) return 'NFL schedule unavailable';
  const opponent=(game.location==='away'?'@ ':game.location==='neutral'?'vs. ':'vs. ')+game.opponent+(game.location==='neutral'?' (neutral)':'');
  if (!game.kickoff) return opponent+' · '+(game.date || '')+' · Kickoff TBD';
  const date=new Date(game.kickoff);
  if (!Number.isFinite(date.getTime())) return opponent+' · Kickoff TBD';
  return opponent+' · '+new Intl.DateTimeFormat(undefined,{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(date);
}
function playerGroup(p){return p.slot==='BN'?'bench':['IR','IR+','NA'].includes(p.slot)?'reserve':'starters';}
function orderedPlayers(players){
  const rank=p=>playerGroup(p)==='bench'?100:playerGroup(p)==='reserve'?200:p.slot==='K'?80:['DEF','D/ST','DST'].includes(p.slot)?90:0;
  return [...players].sort((a,b)=>rank(a)-rank(b));
}
function render() { $('comparison').replaceChildren(); for (const team of current.teams) { const card = el('article', 'team-card' + (team.mine ? '' : ' opponent')); const top = el('div', 'team-top'); top.append(el('div', 'eyebrow', team.mine ? 'SELECTED TEAM' : 'OPPONENT'), el('h2', '', team.name)); if(siteSession?.authenticated && team.manager) top.append(el('div','team-manager','Manager · '+team.manager)); const total = el('div', 'score', score(team.points)); total.append(el('small', '', 'ACTUAL POINTS')); top.append(total, el('div', 'projection', `Yahoo projection: ${score(team.projected)}`)); card.append(top); const title = el('div', 'roster-title'); title.append(el('span', '', 'LINEUP / PLAYER'), el('span', '', 'POINTS')); card.append(title); let lastGroup='starters'; for (const p of orderedPlayers(team.players)) { const group=playerGroup(p); if(group!==lastGroup){if(group!=='starters') card.append(el('div','roster-section',group==='bench'?'BENCH · NOT IN STARTING LINEUP':'RESERVE · NOT IN STARTING LINEUP'));lastGroup=group;} const row = el('button', 'player'+(group==='bench'?' benched':group==='reserve'?' reserved':'')); row.setAttribute('aria-label', `View statistics for ${p.name}`); const info = el('div'); info.append(el('div', 'player-name', p.name)); const meta = el('div', 'player-meta', `${p.team} · ${p.position}`); if(p.status){const status=el('span','status',p.status);const explanation=[p.statusFull,p.injuryNote || 'Yahoo has not provided a specific injury reason.'].filter(Boolean).join(' · ');status.title=explanation;status.tabIndex=0;status.setAttribute('aria-label',p.status+': '+explanation);status.append(el('span','injury-tooltip',explanation));meta.append(status);} info.append(meta,el('div', 'player-game', gameLine(p))); row.append(el('span', 'slot', p.slot || p.position), avatar(p), info, el('span', 'points', score(p.points))); row.onclick = () => showPlayer(p); card.append(row); } if (!team.players.length) card.append(el('p', 'empty', 'No roster available for this week.')); $('comparison').append(card); } }
async function loadMatchup() { const id = ++requestId; current = null; $('comparison').replaceChildren(); $('ai-result').textContent = ''; $('analyze').disabled = true; const p = params(); if (!p.team || !p.week) return; message('Loading this week’s matchup…'); $('prev').disabled = $('week').selectedIndex <= 0; $('next').disabled = $('week').selectedIndex >= $('week').options.length - 1; try { const data = await api('/api/matchup?' + new URLSearchParams(p)); if (id !== requestId) return; current = data; render(); message(data.teams.length ? `Week ${data.week} · ${data.status || 'Scheduled'} · Scores as of ${new Date(data.scoresUpdated||data.updated).toLocaleString()} | Yahoo is not queried by visitors` : data.status); aiButton(); if (siteSession) { await loadBriefings(); if (typeof loadManagement === 'function') await loadManagement(); } } catch (e) { if (id === requestId) message(e.message, true); } }
function teamOptionLabel(team) { return team.name + (siteSession?.authenticated && siteSession.user.team_key===team.key ? ' (you)' : ''); }
async function loadLeague() { const id = ++requestId; current = null; $('comparison').replaceChildren(); $('team').replaceChildren(); $('analyze').disabled = true; const league = leagues.find(l => l.key === $('league').value); if (!league) return; options($('week'), Array.from({length: Number(league.endWeek) - Number(league.startWeek) + 1}, (_, i) => i + Number(league.startWeek)), n => n, n => n); $('week').value = league.week; if (!$('week').value) $('week').selectedIndex = 0; message('Finding your team…'); try { const teams = await api('/api/teams?league=' + encodeURIComponent(league.key)); if (id !== requestId) return; options($('team'), teams, t => teamOptionLabel(t), t => t.key); const mine = teams.find(t => t.key===siteSession?.user.team_key) || teams.find(t => t.mine); if (mine) $('team').value = mine.key; await loadMatchup(); } catch (e) { if (id === requestId) message(e.message, true); } }
$('league').onchange = loadLeague; $('team').onchange = loadMatchup; $('week').onchange = loadMatchup;
$('prev').onclick = () => { if ($('week').selectedIndex > 0) { $('week').selectedIndex--; loadMatchup(); } };
$('next').onclick = () => { if ($('week').selectedIndex < $('week').options.length - 1) { $('week').selectedIndex++; loadMatchup(); } };
$('refresh').onclick = async () => { if (leagues.length) await loadMatchup(); else await init(); };
$('close').onclick = () => $('player-dialog').close();
let aiBusy = false;
function aiButton() { $('analyze').disabled = !aiEnabled || aiBusy || !current?.teams.length || (!!siteSession && !siteSession.user.admin && $('team').value!==siteSession.user.team_key); $('analyze').title=siteSession && !siteSession.user.admin ? 'Generate a private report for your assigned team. Shared monthly spending limits apply.' : 'Generate a private report for this matchup.'; }
async function aiSession() {
  const state = await api('/api/ai/session');
  $('ai-panel').hidden = !state.unlocked;
  if (!state.unlocked) $('ai-result').textContent = '';
  $('ai-unlock').closest('section').hidden = true;
  $('ai-lock').hidden = true;
  aiEnabled = state.unlocked && state.ready;
  if (state.unlocked) {
    $('ai-budget').textContent = `${state.attempts} of ${state.monthlyLimit} monthly attempts | ${state.maxOutputTokens}-token cap | Manual requests only`;
    $('ai-auth-message').textContent = '';
    if (!state.ready && !$('ai-result').textContent) $('ai-result').textContent = siteSession?.user.admin ? 'Add the Azure API key and enable analysis in .env, then recreate the container.' : 'Ask an admin to assign your team in Manage users to generate private reports.';
  }
  aiButton();
}
$('analyze').onclick = async () => {
  if (aiBusy) return;
  const id = requestId; aiBusy = true; aiButton(); $('ai-result').textContent = 'Building your scouting report. One Azure request, no automatic retries...';
  try {
    const job=await api('/api/analytics/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params())});
    const started=Date.now();let r;
    while(true){
      await new Promise(resolve=>setTimeout(resolve,2500));
      const state=await api('/api/analytics/jobs/'+encodeURIComponent(job.id));
      if(state.status==='complete'){r=state.result;break;}
      if(state.status==='failed')throw Error(state.result?.error||'Analysis failed. No automatic retry was made.');
      if(state.status==='interrupted')throw Error('Report processing was interrupted. Check saved briefings before generating again.');
      if(Date.now()-started>180000)throw Error('Report is still processing. Check saved briefings shortly; do not generate another report yet.');
      if(id===requestId)$('ai-result').textContent=`Building your private report · ${Math.round((Date.now()-started)/1000)} seconds. One Azure request; status checks use no AI credits.`;
    }
    if (id === requestId && !$('ai-panel').hidden) { renderReport($('ai-result'), r); await loadBriefings(); }
  } catch(e) {
    if (id === requestId) $('ai-result').textContent = e.message;
    if (e.status === 401) { $('ai-result').textContent = ''; await aiSession(); }
  } finally { aiBusy = false; await aiSession(); }
};
async function init() { try { const config = await siteReady; $('ai-unlock').closest('section').hidden = true; if (config.mode === 'private') await aiSession(); else { $('ai-panel').hidden = true; aiEnabled = false; } leagues = await api('/api/leagues'); options($('league'), leagues, l => `${l.name} · ${l.season}`, l => l.key); if (!leagues.length) return message('No current NFL leagues found for this Yahoo account.', true); await loadLeague(); } catch(e) { message(e.message, true); } }
init();
setInterval(() => { if (siteSession && !$('ai-panel').hidden) aiSession().catch(() => { location.assign('/'); }); }, 60000);


let historyCursor = null, historyRequest = 0;
async function loadBriefings(older=false) {
  if (!siteSession) return;
  const id=++historyRequest, matchupId=requestId;
  $('briefing-history').hidden=false;
  const p=params(); if (!p.team || !p.week) return;
  if (!older) { $('history-list').replaceChildren(); historyCursor=null; $('history-more').hidden=true; }
  try {
    const q=new URLSearchParams(p); if (older && historyCursor) q.set('before',historyCursor);
    const data=await api('/api/briefings?'+q);
    if (id!==historyRequest || matchupId!==requestId) return;
    if (!data.items.length && !older) $('history-list').append(el('p','','No saved briefings for this matchup yet. Generate a report to save the first one.'));
    for (const item of data.items) {
      const row=el('div','history-row');
      const info=el('div'); info.append(el('strong','',new Date(item.generated).toLocaleString()));
      info.append(el('div','player-meta',`${item.season} | Week ${item.week} | ${JSON.parse(item.team_names).join(' vs ')} | ${item.creator}`));
      const button=el('button','','Open briefing');
      button.onclick=async () => {
        const selected=requestId; button.disabled=true;
        try {
          const report=await api('/api/briefings/'+item.id);
          if (selected!==requestId) return;
          renderReport($('ai-result'),report);
          $('ai-result').scrollIntoView({behavior:'smooth',block:'start'});
        } catch(e) { if (selected===requestId) $('ai-result').textContent=e.message; }
        finally { button.disabled=false; }
      };
      const compare=el('button','','Compare with previous');
      compare.onclick=async()=>{try { const r=await api('/api/briefings/'+item.id+'/compare'); const box=el('pre','briefing-diff',r.previous ? (r.changes.join('\n') || 'No report text changes.') : 'No earlier briefing for this matchup.'); row.querySelector('pre')?.remove();row.append(box); } catch(e) { message(e.message,true); }};
      row.append(info,button,compare); $('history-list').append(row);
    }
    historyCursor=data.nextBefore; $('history-more').hidden=!historyCursor;
  } catch(e) { if (id===historyRequest && matchupId===requestId) $('history-list').replaceChildren(el('p','',e.message)); }
}
$('history-refresh').onclick=()=>loadBriefings();
$('history-more').onclick=()=>loadBriefings(true);

// Browser polling reads cached data only. It preserves reports and open tool forms.
let scorePollBusy=false;
setInterval(async()=>{
  if(document.hidden || scorePollBusy || !current || aiBusy) return;
  scorePollBusy=true;const id=requestId;
  try {
    const live=await api('/api/live');
    if(!live.enabled) return;
    const data=await api('/api/matchup?'+new URLSearchParams(params()));
    if(id!==requestId) return;
    current=data;render();
    const timestamp=new Date(data.scoresUpdated||data.updated).toLocaleString();
    message(`Week ${data.week} · ${data.status||'Scheduled'} · Scores as of ${timestamp} · ${live.active?'Live scoring enabled · backend interval 5 minutes':'Live scoring enabled · waiting for a scheduled game window'}`);
  }catch(e){ /* Keep the last good scores on transient errors. */ }
  finally{scorePollBusy=false;}
},60000);
