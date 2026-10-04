/** Generate CSP-safe validation and TypeScript declarations from Python DTOs. */
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';
import standalone from 'ajv/dist/standalone/index.js';
import { build } from 'esbuild';
import { compile } from 'json-schema-to-typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const schema = JSON.parse(
  await readFile(new URL('../src/lib/contracts.schema.json', import.meta.url), 'utf8'),
);
const check = process.argv.slice(2).join(' ') === '--check';
if (process.argv.length > 2 && !check) throw new Error('Usage: generate-contracts.mjs [--check]');
const ajv = new Ajv({
  strict: true,
  strictNumbers: true,
  code: { source: true, esm: true, optimize: true },
  useDefaults: false,
  coerceTypes: false,
  removeAdditional: false,
});
addFormats(ajv);
ajv.addSchema(schema);
const validationSource = standalone(ajv, {
  isSnapshot: `${schema.$id}#/properties/snapshot`,
  isHistory: `${schema.$id}#/properties/history`,
  isTraces: `${schema.$id}#/properties/traces`,
  isTrace: `${schema.$id}#/properties/trace`,
});
const bundled = await build({
  stdin: {
    contents: validationSource,
    resolveDir: root,
    sourcefile: 'standalone-validators.js',
    loader: 'js',
  },
  bundle: true,
  platform: 'browser',
  format: 'esm',
  target: 'es2022',
  write: false,
  legalComments: 'none',
  minify: true,
});
const outputs = new Map([
  [
    'contracts.ts',
    await compile(schema, 'DashboardContracts', {
      bannerComment:
        '/** Generated from Python management DTOs. Run npm run contracts; do not edit. */',
      unknownAny: true,
      additionalProperties: false,
      style: { singleQuote: true, semi: true },
    }),
  ],
  [
    'validators.js',
    '/** Generated standalone validators; no eval or runtime schema compilation. */\n' +
      bundled.outputFiles[0].text,
  ],
  [
    'validators.d.ts',
    `/** Generated guards share the exact schema used to generate contracts.ts. */
import type { ManagementSnapshot, PerformancePoint, TraceSummary, TraceDetail } from './contracts';
export declare function isSnapshot(data: unknown): data is ManagementSnapshot;
export declare function isHistory(data: unknown): data is PerformancePoint[];
export declare function isTraces(data: unknown): data is TraceSummary[];
export declare function isTrace(data: unknown): data is TraceDetail;
`,
  ],
]);
for (const [name, contents] of outputs) {
  const path = new URL(`../src/lib/${name}`, import.meta.url);
  if (check) {
    if ((await readFile(path, 'utf8')) !== contents)
      throw new Error(`Stale dashboard contract: ${name}`);
  } else {
    await writeFile(path, contents);
  }
}
