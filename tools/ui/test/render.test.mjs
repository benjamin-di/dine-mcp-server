import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { render, safeLink } from '../render.js';
const query = {date_from:'2027-01-02', date_to:'2027-01-02', time_from:'23:00',time_to:'01:00',party_size:2};
const row = {venue_name:'Synthetic <script>bad()</script>',channel:'synthetic',time_zone:'Asia/Tokyo',status:'ok',slots:[{start:'2027-01-03T00:30+09:00',kind:'bookable',experience:'<img src=x onerror=bad()>'}],coverage:{dates_checked:['2027-01-02'],dates_not_checked:['2027-01-03']}, booking_link:{url:'https://www.tablecheck.com/en/shops/synthetic/reserve'}};
function document() { return new JSDOM('<p id="summary"></p><div id="grid"></div>').window.document; }
test('overnight slots belong to service day; incomplete coverage is unknown; text stays text',()=>{
  const doc=document();render(doc,{query,results:[row]},()=>{});
  assert.match(doc.body.textContent,/00:30/);assert.match(doc.body.textContent,/Next day 2027-01-03/);
  assert.match(doc.body.textContent,/Coverage incomplete/);assert.match(doc.body.textContent,/<script>bad/);
  assert.equal(doc.querySelectorAll('script,img').length,0);assert.equal(doc.querySelectorAll('td').length,1);
});
test('filters operate locally and booking page opens through host',()=>{
  const doc=document(), opened=[];render(doc,{query,results:[row]},url=>opened.push(url));
  doc.querySelector('button').click();assert.deepEqual(opened,[row.booking_link.url]);
  render(doc,{query,results:[row]},()=>{},{onlyAvailable:true,kind:'request'});
  assert.match(doc.body.textContent,/No venues match/);
});
test('unsafe links never become navigation',()=>{
  for(const url of ['javascript:bad()', 'http://www.example.com/', 'https://user:pass@www.example.com/', 'https://www.example.com/\n']) assert.equal(safeLink(url),null);
});
test('party limits and contact remain visible without slots',()=>{
  const doc=document(); render(doc,{query,results:[{...row,status:'party_too_small',min_party:4,contact:'Synthetic contact',slots:[]}]},()=>{});
  assert.match(doc.body.textContent,/Online minimum: 4/);assert.match(doc.body.textContent,/Synthetic contact/);
});
