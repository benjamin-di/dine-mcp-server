import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { setTimeout } from 'node:timers/promises';
import { JSDOM, VirtualConsole } from 'jsdom';

// Execute the actual packaged SDK bundle against a synthetic MCP Apps host. No remote resources.
test('packaged app initializes, receives tool results, filters locally and asks host to open a link', async () => {
  const html = await readFile(new URL('../../../src/dine_mcp/ui/availability.html', import.meta.url), 'utf8');
  const dom = new JSDOM(html.replace('type="module"', ''), {
    runScripts: 'dangerously', pretendToBeVisual: true, url: 'https://www.example.com/', virtualConsole: new VirtualConsole(),
    beforeParse(window) {
      window.TextEncoder = TextEncoder; window.TextDecoder = TextDecoder;
      window.structuredClone = structuredClone;
      window.fetch = () => { throw new Error('The app must not fetch data'); };
      window.ResizeObserver = class { observe() {} disconnect() {} };
    },
  });
  const { window } = dom;
  const sent = [];
  // SDK handshake starts asynchronously after document scripts run.
  const original = window.postMessage.bind(window);
  window.postMessage = message => {
    sent.push(message);
    if (message.method === 'ui/initialize') {
      queueMicrotask(() => window.dispatchEvent(new window.MessageEvent('message', {
        source: window, data: { jsonrpc: '2.0', id: message.id, result: {
          protocolVersion: '2026-01-26', hostInfo: { name: 'Synthetic host', version: '1.0.0' },
          hostCapabilities: { openLinks: {} }, hostContext: { theme: 'dark' },
        } },
      })));
    } else if (message.id !== undefined) {
      queueMicrotask(() => window.dispatchEvent(new window.MessageEvent('message', {
        source: window, data: {jsonrpc:'2.0', id:message.id, result:{}},
      })));
    }
  };
  try {
    for (let n = 0; n < 100 && !sent.some(m => m.method === 'ui/notifications/initialized'); n++) await setTimeout(5);
    assert.ok(sent.some(m => m.method === 'ui/initialize'), 'SDK initialization request');
    assert.ok(sent.some(m => m.method === 'ui/notifications/initialized'), 'SDK completed initialization');
    assert.equal(window.document.documentElement.getAttribute('data-theme'), 'dark');
    window.dispatchEvent(new window.MessageEvent('message', { source: window, data: {
      jsonrpc:'2.0', method:'ui/notifications/tool-result', params:{content:[], structuredContent:{
        query:{date_from:'2027-01-02', date_to:'2027-01-02',time_from:'18:00',time_to:'22:00',party_size:2},
        results:[{venue_name:'Synthetic Bistro',channel:'zenchef',status:'ok',slots:[{start:'2027-01-02T19:00+01:00',kind:'bookable'}],coverage:{dates_checked:['2027-01-02']},booking_link:{url:'https://bookings.zenchef.com/results?rid=123456'}}],
      }},
    }}));
    await setTimeout(20);
    assert.match(window.document.getElementById('grid').textContent, /Synthetic Bistro.*19:00/s);
    window.document.querySelector('button').click();
    await setTimeout(10);
    assert.equal(sent.find(m => m.method === 'ui/open-link')?.params.url, 'https://bookings.zenchef.com/results?rid=123456');
    const before = sent.length;
    window.document.getElementById('kind').value = 'waitlist';
    window.document.getElementById('kind').dispatchEvent(new window.Event('change'));
    assert.doesNotMatch(window.document.getElementById('grid').textContent, /19:00/);
    assert.equal(sent.length, before, 'filtering sends no host or tool requests');
    assert.ok(!sent.some(m => m.method?.startsWith('tools/')));
  } finally { window.postMessage = original; window.close(); }
});
