import assert from 'node:assert/strict';
import test from 'node:test';

import {
  filterDiagnosticsForChangedFiles,
  isFrontendTypeScriptPath,
  parseDiagnosticPath,
  toTypeScriptDiagnosticPath,
} from './type-check-changed.mjs';

test('recognizes frontend TypeScript source and config files from repo-relative paths', () => {
  assert.equal(isFrontendTypeScriptPath('frontend/src/App.tsx'), true);
  assert.equal(isFrontendTypeScriptPath('frontend/vite.config.ts'), true);
  assert.equal(isFrontendTypeScriptPath('frontend/src/types.d.ts'), true);
  assert.equal(isFrontendTypeScriptPath('frontend/tsconfig.json'), true);
  assert.equal(isFrontendTypeScriptPath('frontend/tsconfig.node.json'), true);
  assert.equal(isFrontendTypeScriptPath('backend/src/app.py'), false);
  assert.equal(isFrontendTypeScriptPath('frontend/package.json'), false);
  assert.equal(isFrontendTypeScriptPath('frontend/src/tsconfig.json'), false);
});

test('converts repo-relative frontend paths to tsc diagnostic paths', () => {
  assert.equal(
    toTypeScriptDiagnosticPath('frontend/src/components/Chat.tsx'),
    'src/components/Chat.tsx',
  );
});

test('parses TypeScript diagnostic paths', () => {
  assert.equal(
    parseDiagnosticPath(
      "src/App.tsx(10,5): error TS6133: 'unused' is declared but its value is never read.",
    ),
    'src/App.tsx',
  );
  assert.equal(
    parseDiagnosticPath('src/App.tsx: error TS2304: Cannot find name x.'),
    'src/App.tsx',
  );
  assert.equal(parseDiagnosticPath('error TS18003: No inputs were found.'), null);
});

test('separates changed-file diagnostics from existing repo-wide diagnostics', () => {
  const output = [
    "src/Changed.tsx(1,1): error TS6133: 'React' is declared but its value is never read.",
    "src/Existing.tsx(2,1): error TS2304: Cannot find name 'missing'.",
    'error TS18003: No inputs were found in config file.',
  ].join('\n');

  const result = filterDiagnosticsForChangedFiles(output, ['src/Changed.tsx']);

  assert.deepEqual(result.changedDiagnostics, [
    "src/Changed.tsx(1,1): error TS6133: 'React' is declared but its value is never read.",
  ]);
  assert.deepEqual(result.existingDiagnostics, [
    "src/Existing.tsx(2,1): error TS2304: Cannot find name 'missing'.",
  ]);
  assert.deepEqual(result.globalDiagnostics, [
    'error TS18003: No inputs were found in config file.',
  ]);
});

test('treats changed TypeScript config diagnostics as blocking', () => {
  const output = [
    "tsconfig.json(3,5): error TS5023: Unknown compiler option 'badOption'.",
    "src/Existing.tsx(2,1): error TS2304: Cannot find name 'missing'.",
    'error TS18003: No inputs were found in config file.',
  ].join('\n');

  const result = filterDiagnosticsForChangedFiles(output, ['tsconfig.json']);

  assert.deepEqual(result.changedDiagnostics, [
    "tsconfig.json(3,5): error TS5023: Unknown compiler option 'badOption'.",
  ]);
  assert.deepEqual(result.existingDiagnostics, [
    "src/Existing.tsx(2,1): error TS2304: Cannot find name 'missing'.",
  ]);
  assert.deepEqual(result.globalDiagnostics, [
    'error TS18003: No inputs were found in config file.',
  ]);
});
