import {build} from 'esbuild';
import {readFile, readdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));
const {metafile} = await build({
  absWorkingDir: directory,
  entryPoints: ['engine.mjs'],
  outfile: '../editor-engine.js',
  bundle: true,
  format: 'iife',
  globalName: 'PrismaEditorEngine',
  minify: true,
  legalComments: 'eof',
  metafile: true,
});
const modules = new Set(Object.keys(metafile.inputs).filter((file) => file.startsWith('node_modules/')).map((file) => {
  const parts = file.split('/');
  return parts.slice(0, parts[1].startsWith('@') ? 3 : 2).join('/');
}));
const licenses = await Promise.all([...modules].sort().map(async (module) => {
  const location = path.join(directory, module);
  const manifest = JSON.parse(await readFile(path.join(location, 'package.json'), 'utf8'));
  const listing = await readdir(location);
  const license = listing.find((name) => /^licen[sc]e(\.|$)/i.test(name));
  if (!license) throw new Error(`License file missing for ${manifest.name}`);
  return `${manifest.name} ${manifest.version}\n${await readFile(path.join(location, license), 'utf8')}`;
}));
await writeFile(path.join(directory, '../editor-engine.LICENSE.txt'), licenses.join('\n\n'));
