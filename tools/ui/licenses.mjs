import { readFile } from 'node:fs/promises';
const packages = [
  ['@modelcontextprotocol/ext-apps', 'LICENSE'],
  ['@modelcontextprotocol/client', 'LICENSE'],
  ['@modelcontextprotocol/core', 'LICENSE'],
  ['zod', 'LICENSE'], ['pkce-challenge', 'LICENSE'],
  ['eventsource-parser', 'LICENSE'], ['eventsource', 'LICENSE'], ['jose', 'LICENSE.md'],
];
export async function notices(directory) {
  const output = ['Third-party software included in the Dine MCP availability app.\n'];
  for (const [name, file] of packages) {
    const root = new URL(`node_modules/${name}/`, directory);
    const pkg = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));
    output.push(`\n${name} ${pkg.version} (${pkg.license})\n${await readFile(new URL(file, root), 'utf8')}`);
  }
  const text = output.join('\n').replace(/[ \t]+$/gm, '');
  const escaped = text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('@', '&#64;');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><title>Dine App third-party licences</title><pre>${escaped}</pre></html>\n`;
}
