/* Small Markdown renderer. Model output is always text, never executable HTML. */
function reportInline(node, text) {
  for (const chunk of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if ((chunk.startsWith('**') && chunk.endsWith('**')) || (chunk.startsWith('`') && chunk.endsWith('`'))) {
      const bold = chunk.startsWith('**');
      const span = document.createElement(bold ? 'strong' : 'code');
      span.textContent = chunk.slice(bold ? 2 : 1, bold ? -2 : -1); node.append(span);
    } else node.append(document.createTextNode(chunk));
  }
}
function renderReport(container, response) {
  container.replaceChildren();
  const heading = document.createElement('div'); heading.className = 'report-topline';
  heading.textContent = 'MATCHUP BRIEFING | ' + new Date(response.saved?.generated || Date.now()).toLocaleString();
  if (response.saved) heading.textContent += ` | Week ${response.saved.week} | ${response.saved.creator} | Snapshot: ${new Date(response.saved.snapshot_at).toLocaleString()}`;
  container.append(heading);
  const grid = document.createElement('div'); grid.className = 'report-grid'; container.append(grid);
  let section = null, list = null;
  const makeSection = title => {
    section = document.createElement('section'); section.className = 'report-card';
    if (/verdict|checklist|kickoff/i.test(title)) section.classList.add('report-wide');
    if (/verdict/i.test(title)) section.classList.add('report-verdict');
    const h = document.createElement('h3'); reportInline(h, title); section.append(h); grid.append(section); list = null;
  };
  const lines = String(response.text || '').replace(/\r/g, '').split('\n');
  const isDivider = line => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || /^```/.test(line) || /^[-*_]{3,}$/.test(line)) { list = null; continue; }
    const match = line.match(/^#{1,3}\s+(.+)$/);
    if (match) { makeSection(match[1]); continue; }
    if (!section) makeSection('Matchup outlook');
    if (line.includes('|') && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const wrap = document.createElement('div'); wrap.className = 'report-table-wrap';
      const table = document.createElement('table'); const thead = document.createElement('thead'); const tr = document.createElement('tr');
      for (const cell of cells(line)) { const th = document.createElement('th'); th.scope = 'col'; reportInline(th, cell); tr.append(th); }
      thead.append(tr); table.append(thead); const tbody = document.createElement('tbody'); i += 2;
      while (i < lines.length && lines[i].trim().includes('|') && !/^#/.test(lines[i].trim())) {
        const row = document.createElement('tr');
        for (const cell of cells(lines[i])) {
          const td = document.createElement('td'); reportInline(td, cell);
          const call = cell.replace(/\*/g, '').trim().toLowerCase();
          if (['start','sit','monitor','ir','low','moderate','high'].includes(call)) td.className = 'report-call call-' + call;
          row.append(td);
        }
        tbody.append(row); i++;
      }
      i--; table.append(tbody); wrap.append(table); section.append(wrap); list = null; continue;
    }
    const bullet = line.match(/^(?:[-*]|\d+[.)])\s+(.+)$/);
    if (bullet) {
      if (!list) { list = document.createElement('ul'); section.append(list); }
      const li = document.createElement('li'); reportInline(li, bullet[1]); list.append(li);
    } else {
      list = null; const p = document.createElement('p'); reportInline(p, line); section.append(p);
    }
  }
  if (response.incomplete) {
    const warning = document.createElement('p'); warning.className = 'report-warning';
    warning.textContent = 'This report reached its output limit and may be incomplete. No extra request was made.'; container.append(warning);
  }
  if (response.saveError) {
    const warning=document.createElement('p'); warning.className='report-warning'; warning.textContent=response.saveError; container.append(warning);
  }
  const meta = document.createElement('div'); meta.className = 'report-meta';
  meta.textContent = `TOKEN USAGE | ${response.usage?.input_tokens ?? 'Unknown'} input / ${response.usage?.output_tokens ?? 'Unknown'} output (includes reasoning) | Generated on demand`;
  container.append(meta);
}
