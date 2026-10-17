/* All tools use private cached endpoints. No Yahoo or Azure calls from these views. */
let managementRequest=0;
async function loadManagement() {
  if (!siteSession) return;
  const id=++managementRequest, selected=requestId;
  $('management').hidden=false;
  try {
    const d=await api('/api/manage?'+new URLSearchParams(params()));
    if(id!==managementRequest || selected!==requestId) return;
    const root=$('management-content');root.replaceChildren();
    const viewed=d.teams.find(t=>t.key===params().team)?.name || 'Selected team';
    $('management-message').textContent=`Viewing ${viewed} · Week ${params().week}. Lineup checks, trends and planning below follow this team. Snapshot: ${new Date(d.updated).toLocaleString()}.`;
    function panel(title,open=false) { const p=el('details','tool-panel');p.open=open;p.append(el('summary','',title));root.append(p);return p; }
    function table(parent,heads,rows) { const wrap=el('div','table-scroll'),t=el('table');const h=el('tr');heads.forEach(x=>h.append(el('th','',x)));t.append(h);rows.forEach(values=>{const r=el('tr');values.forEach(x=>{const c=el('td');if(x instanceof Node)c.append(x);else c.textContent=x??'Unavailable';r.append(c);});t.append(r);});wrap.append(t);parent.append(wrap); }
    function button(label,action) {const b=el('button','',label);b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){$('management-message').textContent=e.message;}finally{b.disabled=false;}};return b;}
    function select(label,players) {const l=el('label','',label),v=el('select');options(v,players,p=>`${p.name} (${p.position||p.key})`,p=>p.key);l.append(v);return [l,v];}
    const alerts=panel('Lineup checks · '+viewed,true);d.alerts.forEach(a=>alerts.append(el('p','status',a)));if(!d.alerts.length)alerts.append(el('p','','No bye or injury designations found among listed starters.'));
    alerts.append(el('p','stats-footnote','Empty-slot checks require cached league roster settings. Verify a full lineup and current injury news before kickoff. Historical injury metadata may reflect today.'));
    const league=panel('League overview');if(d.standings.length) table(league,['Team','Rank','W-L-T','Points for','Points against'],d.standings.map(t=>[t.name,t.rank,`${t.wins}-${t.losses}-${t.ties}`,score(t.for),score(t.against)]));
    league.append(el('h3','','Week '+params().week+' · League matchups'),el('p','stats-footnote','Choose a game to view both rosters above. This also changes the selected team used by the tools below. Viewing a matchup uses no AI credits.'));
    const games=el('div','league-games');league.append(games);
    for(const m of d.matchups) {
      const active=m.teams.some(t=>t.key===params().team),finished=m.status==='postevent';
      const card=el('article','league-game'+(active?' is-selected':''));
      card.append(el('div','eyebrow',finished?'FINAL':m.status==='midevent'?'IN PROGRESS':'SCHEDULED'));
      for(const t of m.teams){const row=el('div','league-game-team');const name=el('span','',t.name+(t.key===params().team?' · viewing':''));const points=el('div','league-game-score');points.append(el('strong','',score(t.points)),el('small','',`Yahoo projection ${score(t.projected)}`));row.append(name,points);card.append(row);}
      const target=m.teams.find(t=>t.key===params().team)||m.teams[0];
      const view=button(active?'View selected matchup ↑':'View matchup ↑',async()=>{
        $('team').value=target.key;await loadMatchup();
        $('comparison').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
        $('comparison').setAttribute('tabindex','-1');$('comparison').focus({preventScroll:true});
      });
      view.setAttribute('aria-label',`View Week ${params().week}: ${m.teams.map(t=>t.name).join(' versus ')}`);
      if(active)view.setAttribute('aria-current','true');card.append(view);games.append(card);
    }

    if(!d.standings.length)league.append(el('p','','Standings will appear after the next backend refresh.'));
    const trends=panel('Recent performance & bench depth');trends.append(el('p','stats-footnote',`Recent average: completed weeks ${d.recentWeeks.join(', ')||'none'} before the selected week. Prior average uses earlier completed cached weeks. Observed weeks include byes and inactive zeroes; these are not per-game averages.`));
    table(trends,['Player / slot','Season points','Recent avg','Prior avg','Weeks observed','Recent stats (totals)'],d.trends.map(p=>[`${p.name} / ${p.slot}`,score(p.season),score(p.recent),score(p.prior),`${p.samples} / ${d.recentWeeks.length}`+(p.missingRecentWeeks?.length ? ` · missing weeks ${p.missingRecentWeeks.join(', ')}` : ''),Object.entries(p.volume).map(([k,v])=>`${k}: ${v??'unavailable'}`).join(' | ') || 'No applicable stats cached']));
    const byes=panel('Bye-week planner');table(byes,['Week','Players'],Object.entries(d.byes).sort((a,b)=>Number(a[0])-Number(b[0])).map(([w,n])=>[w,n.join(', ')]));
    const waivers=panel('Available players & watchlist');waivers.append(el('p','stats-footnote','Top 25 available players by Yahoo season fantasy points at snapshot time. Availability and waiver eligibility can change. Watchlists are personal.'));
    table(waivers,['Player','Position','Season points','Action'],d.available.map(p=>[p.name,p.position,score(p.points),button('Watch',async()=>{await api('/api/manage/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...params(),kind:'watch',player:p.key})});await loadManagement();})]));
    const ownNotes=d.notes.filter(n=>n.league===params().league);
    for(const n of ownNotes.filter(n=>n.kind==='watch')) {const r=el('p','');r.append(el('span','',`${n.names[n.player]} · saved ${new Date(n.created).toLocaleDateString()} `),button('Remove',async()=>{await api('/api/manage/notes/'+n.id,{method:'DELETE',headers:{'Content-Type':'application/json'},body:'{}'});await loadManagement();}));waivers.append(r);}
    const trade=panel('Trade comparison');trade.append(el('p','stats-footnote','Compare one player from each roster using season totals and roster context. This is not a trade-value prediction; season totals do not account for games played, schedule, injuries or positional scarcity.'));
    const [giveLabel,give]=select('PLAYER FROM SELECTED TEAM',d.roster);const otherTeams=d.teams.filter(t=>t.key!==params().team);const tl=el('label','','OTHER TEAM'),ts=el('select');options(ts,otherTeams,t=>t.name,t=>t.key);tl.append(ts);const [getLabel,get]=select('RECEIVE',[]);function updateTrade(){options(get,d.allRosters[ts.value]||[],p=>`${p.name} (${p.position})`,p=>p.key);}ts.onchange=updateTrade;updateTrade();const tr=el('div');
    const tradeForm=el('div','tool-form');trade.append(tradeForm);tradeForm.append(giveLabel,tl,getLabel,button('Compare players',async()=>{const a=d.trends.find(p=>p.key===give.value),b=(d.allRosters[ts.value]||[]).find(p=>p.key===get.value);if(!a||!b)return;const stat=await api('/api/player-stats?'+new URLSearchParams({league:params().league,player:b.key,mode:'season'}));tr.replaceChildren(el('p','',`${a.name} (${a.position}): ${score(a.season)} season points. ${b.name} (${b.position}): ${score(stat.points)} season points.`),el('p','',`The selected roster currently has ${d.roster.filter(p=>p.position===a.position).length} players at ${a.position} and ${d.roster.filter(p=>p.position===b.position).length} at ${b.position}. Check eligibility and bye coverage before proposing a trade.`));}));trade.append(tr);
    const decisions=panel('Lineup decisions & review');decisions.append(el('p','stats-footnote','Record a start/sit decision before kickoff. Completed-week reviews compare actual Yahoo points; they do not prove a decision was wrong. Entries are personal.'));
    const [pl,pv]=select('START',d.roster),[al,av]=select('SIT / ALTERNATIVE',d.roster);const rl=el('label','','REASON'),reason=el('input');reason.maxLength=500;reason.placeholder='Why this lineup choice?';rl.className='reason-field';rl.append(reason);
    const decisionForm=el('div','tool-form decision-form');decisions.append(decisionForm);decisionForm.append(pl,al,rl,button('Save decision',async()=>{await api('/api/manage/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...params(),kind:'decision',player:pv.value,alternative:av.value,reason:reason.value})});await loadManagement();}));
    for(const n of ownNotes.filter(n=>n.kind==='decision')){const r=el('div','decision-row');r.append(el('strong','',`${n.season} Week ${n.week}: start ${n.names[n.player]}, sit ${n.names[n.alternative]}`),el('p','',n.reason),el('p','',n.review?`Final points: starter ${score(n.review.started)} / alternative ${score(n.review.alternative)}`:'Review pending a completed-week snapshot.'),button('Delete',async()=>{await api('/api/manage/notes/'+n.id,{method:'DELETE',headers:{'Content-Type':'application/json'},body:'{}'});await loadManagement();}));decisions.append(r);}
    if(siteSession.user.admin){
      const budget=panel('Admin AI budget');
      budget.append(el('p','','Shared across all users. Attempts reset monthly (UTC); saving settings does not reset usage. Failed requests count as attempts. Token cap includes report output and reasoning, not input tokens. Lower limits can truncate reports. Set attempts to 0 to pause generation.'));
      const limits=await api('/api/admin/ai-settings');
      const form=el('div','tool-form'),monthlyLabel=el('label','','MONTHLY ATTEMPTS'),monthly=el('input'),tokenLabel=el('label','','OUTPUT / REASONING TOKEN CAP'),tokens=el('input');
      monthly.type=tokens.type='number';monthly.min=0;monthly.max=10000;tokens.min=512;tokens.max=8192;monthly.step=tokens.step=1;monthly.value=limits.monthlyLimit;tokens.value=limits.maxOutputTokens;monthlyLabel.append(monthly);tokenLabel.append(tokens);
      const budgetMessage=el('p','');
      form.append(monthlyLabel,tokenLabel,button('Save AI limits',async()=>{
        if(!monthly.checkValidity() || !tokens.checkValidity()){budgetMessage.textContent='Enter whole numbers within the allowed ranges: attempts 0–10000, tokens 512–8192.';return;}
        await api('/api/admin/ai-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({monthlyLimit:Number(monthly.value),maxOutputTokens:Number(tokens.value)})});
        budgetMessage.textContent='Saved. New reports use these limits immediately; current usage is unchanged.';await aiSession();
      }));budget.append(form,budgetMessage);
      const cadence=panel('Admin data refresh schedule');
      cadence.append(el('p','','Full Yahoo data refreshes run automatically at this interval. Live scoring has its own five-minute schedule. Changing this setting does not call Azure or immediately refresh data.'));
      const scheduleState=await api('/api/admin/refresh-settings');
      const scheduleLabel=el('label','','REFRESH EVERY'),hours=el('select');
      options(hours,[1,2,3,4,6,8,12,24],n=>`${n} hour${n===1?'':'s'}`,n=>n);hours.value=String(scheduleState.hours);scheduleLabel.append(hours);
      const scheduleMessage=el('p','','Current interval: '+scheduleState.hours+' hours');
      cadence.append(scheduleLabel,button('Save refresh interval',async()=>{
        const result=await api('/api/admin/refresh-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hours:Number(hours.value)})});
        scheduleMessage.textContent='Saved: every '+result.hours+' hours. The worker checks the schedule every 30 seconds.';
      }),scheduleMessage);
      const live=panel('Admin live scoring');
      live.append(el('p','','Optional five-minute score updates during scheduled NFL game windows (15 minutes before kickoff through six hours afterward). Off outside these windows. No Azure calls. A daily budget and error backoff limit Yahoo traffic. Full scheduled refreshes remain separate.'));
      const liveInfo=el('p','','Loading setting…');live.append(liveInfo);
      try {
        const setting=await api('/api/admin/live');
        liveInfo.textContent=`Live scoring ${setting.enabled?'ON':'OFF'} · ${setting.calls} of 2000 budget units today (includes retry allowance).`;
        live.append(button(setting.enabled?'Turn live scoring off':'Turn live scoring on',async()=>{
          await api('/api/admin/live',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!setting.enabled})});
          await loadManagement();
        }));
      }catch(e){liveInfo.textContent=e.message;}
      const refresh=panel('Admin game-day refresh');refresh.append(el('p','','Full refreshes follow your configured interval. Manual refresh is limited to one every 15 minutes and four per rolling 24 hours. Refreshing does not generate AI reports.'),el('p','',d.refresh?`Latest request: ${d.refresh.status} · ${new Date(d.refresh.requested*1000).toLocaleString()}`:'No manual refresh requested.'),button('Request Yahoo refresh',async()=>{const r=await api('/api/admin/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});$('management-message').textContent=r.message;}),button('Check refresh status',async()=>{await loadMatchup();}));}
  }catch(e){if(id===managementRequest)$('management-message').textContent=e.message;}
}
