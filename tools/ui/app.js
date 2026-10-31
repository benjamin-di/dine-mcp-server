import { App, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';
import { render } from './render.js';
const app = new App({ name: 'Dine availability', version: '0.2.4' });
let result = null;
function draw() {
  render(document, result, url => app.openLink({ url }).catch(() => {
    document.getElementById('summary').textContent = 'The host could not open the booking page. Use the link in the tool’s text result.';
  }), {
    kind: document.getElementById('kind').value,
    onlyAvailable: document.getElementById('available').checked,
  });
}
app.ontoolresult = event => { result = event.structuredContent; draw(); };
app.onhostcontextchanged = context => { if (context.theme) applyDocumentTheme(context.theme); };
app.onteardown = async () => ({});
document.getElementById('kind').addEventListener('change', draw);
document.getElementById('available').addEventListener('change', draw);
app.connect().then(() => {
  const context = app.getHostContext();
  if (context?.theme) applyDocumentTheme(context.theme);
}).catch(() => {
  document.getElementById('summary').textContent = 'Open this view in an MCP Apps host. The tool’s text result remains available.';
});
