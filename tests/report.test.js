const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this.textContent = ''; this.classList = {add: cls => {this.className += ' ' + cls;}}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
}
const document = {createElement: tag => new Node(tag), createTextNode: text => ({textContent:text})};
const context = vm.createContext({document, Date});
vm.runInContext(fs.readFileSync('public/report.js','utf8'), context);
const target = new Node('div');
context.renderReport(target, {text:'## Matchup verdict\nYour team: **Moderate** confidence.\n\n## Position matchups\n| Position | Edge |\n| --- | --- |\n| QB | Opponent |\n\n## Player-by-player checklist\n| Player | Slot | Call | Reason |\n| --- | --- | --- | --- |\n| <img src=x onerror=alert(1)> | QB | Monitor | Verify status |', usage:{input_tokens:123,output_tokens:456},incomplete:true});
const all = [];
function walk(n) { all.push(n); for (const c of n.children || []) walk(c); }
walk(target);
assert.equal(all.filter(n => n.tagName === 'table').length, 2);
assert.equal(all.filter(n => n.tagName === 'strong').length, 1);
assert.ok(all.some(n => n.className?.includes('report-verdict')));
assert.ok(all.some(n => n.className?.includes('call-monitor')));
assert.ok(all.some(n => n.textContent === '<img src=x onerror=alert(1)>'));
assert.ok(!all.some(n => ['img','script','iframe'].includes(n.tagName)));
assert.ok(all.some(n => n.className === 'report-warning'));
assert.ok(all.some(n => n.className === 'report-meta' && n.textContent.includes('123 input / 456 output')));
console.log('Report renderer: tables, headings, emphasis, calls, truncation, usage and HTML safety passed.');
