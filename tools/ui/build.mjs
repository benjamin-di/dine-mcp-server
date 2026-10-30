import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { notices } from './licenses.mjs';
const directory = new URL('.', import.meta.url);
const inputs = ['app.js', 'render.js', 'template.html', 'package.json', 'package-lock.json', 'build.mjs', 'licenses.mjs'];
const hash = createHash('sha256');
for (const name of inputs) hash.update(await readFile(new URL(name, directory)));
const result = await build({ absWorkingDir: directory.pathname, entryPoints: ['app.js'], bundle: true, format: 'esm', target: 'es2022', minify: true, write: false, legalComments: 'inline' });
const template = await readFile(new URL('template.html', directory), 'utf8');
const script = result.outputFiles[0].text.replace(/<\/script/gi, '<\\/script');
const html = `<!-- source-sha256: ${hash.digest('hex')} -->\n` + template.replace('/* BUNDLE */', () => script);
for (const [name, text] of [['availability.html', html], ['THIRD_PARTY_LICENSES.html', await notices(directory)]]) {
  const destination = new URL(`../../src/dine_mcp/ui/${name}`, directory);
  if (process.argv.includes('--check')) {
    if (await readFile(destination, 'utf8') !== text) throw new Error(`${name} is stale; run make ui`);
  } else await writeFile(destination, text);
}
console.log(`Self-contained availability app ${process.argv.includes('--check') ? 'verified' : 'built'} (${Buffer.byteLength(html)} bytes)`);
