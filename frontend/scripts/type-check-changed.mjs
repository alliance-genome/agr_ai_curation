#!/usr/bin/env node

import { execFileSync, spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const frontendRoot = path.resolve(path.dirname(scriptPath), '..');

function execGit(args, cwd) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

export function normalizeDiagnosticPath(filePath) {
  return filePath.replaceAll(path.sep, '/').replace(/^\.\//, '');
}

export function isFrontendTypeScriptPath(repoRelativePath) {
  const normalized = normalizeDiagnosticPath(repoRelativePath);
  return (
    normalized.startsWith('frontend/') &&
    /\.(?:cts|mts|ts|tsx)$/.test(normalized)
  );
}

export function toTypeScriptDiagnosticPath(repoRelativePath) {
  const normalized = normalizeDiagnosticPath(repoRelativePath);
  return normalized.startsWith('frontend/')
    ? normalized.slice('frontend/'.length)
    : normalized;
}

export function parseDiagnosticPath(line) {
  const locationMatch = line.match(/^(.+?)\(\d+,\d+\):\s+error\s+TS\d+:/);
  if (locationMatch) {
    return normalizeDiagnosticPath(locationMatch[1]);
  }

  const fileMatch = line.match(/^(.+?):\s+error\s+TS\d+:/);
  if (fileMatch) {
    return normalizeDiagnosticPath(fileMatch[1]);
  }

  return null;
}

export function filterDiagnosticsForChangedFiles(output, changedFiles) {
  const changedFileSet = new Set(changedFiles.map(normalizeDiagnosticPath));
  const changedDiagnostics = [];
  const globalDiagnostics = [];
  const existingDiagnostics = [];

  for (const rawLine of output.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (!line || !line.includes('error TS')) {
      continue;
    }

    if (/^error TS\d+:/.test(line)) {
      globalDiagnostics.push(line);
      continue;
    }

    const diagnosticPath = parseDiagnosticPath(line);
    if (diagnosticPath && changedFileSet.has(diagnosticPath)) {
      changedDiagnostics.push(line);
      continue;
    }

    existingDiagnostics.push(line);
  }

  return {
    changedDiagnostics,
    existingDiagnostics,
    globalDiagnostics,
  };
}

function parseArgs(argv) {
  const options = {
    baseRef: process.env.TYPECHECK_BASE || 'origin/main',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--base') {
      const value = argv[index + 1];
      if (!value) {
        throw new Error('--base requires a git ref value.');
      }
      options.baseRef = value;
      index += 1;
      continue;
    }

    if (arg === '--help' || arg === '-h') {
      options.help = true;
      continue;
    }

    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

function printHelp() {
  console.log(`Usage:
  npm run type-check -- [--base <git-ref>]

Runs the repo-wide TypeScript compiler and fails only when diagnostics belong
to frontend TypeScript files changed from the base ref, staged changes,
unstaged changes, or untracked files.

Environment:
  TYPECHECK_BASE    Base ref for committed branch changes. Default: origin/main.

Related:
  npm run type-check:all    Run the full repo-wide TypeScript check.`);
}

function collectChangedFrontendTypeScriptFiles(repoRoot, baseRef) {
  const changedFiles = new Set();
  const addGitOutput = (args) => {
    const output = execGit(args, repoRoot);
    for (const filePath of output.split(/\r?\n/)) {
      if (filePath) {
        changedFiles.add(filePath);
      }
    }
  };

  addGitOutput([
    'diff',
    '--name-only',
    '--diff-filter=ACMR',
    `${baseRef}...HEAD`,
    '--',
    'frontend',
  ]);
  addGitOutput([
    'diff',
    '--name-only',
    '--diff-filter=ACMR',
    '--',
    'frontend',
  ]);
  addGitOutput([
    'diff',
    '--cached',
    '--name-only',
    '--diff-filter=ACMR',
    '--',
    'frontend',
  ]);
  addGitOutput([
    'ls-files',
    '--others',
    '--exclude-standard',
    '--',
    'frontend',
  ]);

  return [...changedFiles]
    .filter(isFrontendTypeScriptPath)
    .map(toTypeScriptDiagnosticPath)
    .sort();
}

function runTypeScriptCompiler() {
  const tscBin = path.join(
    frontendRoot,
    'node_modules',
    '.bin',
    process.platform === 'win32' ? 'tsc.cmd' : 'tsc',
  );

  return spawnSync(tscBin, ['--noEmit', '--pretty', 'false'], {
    cwd: frontendRoot,
    encoding: 'utf8',
  });
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }

  const repoRoot = execGit(['rev-parse', '--show-toplevel'], frontendRoot).trim();
  const changedFiles = collectChangedFrontendTypeScriptFiles(
    repoRoot,
    options.baseRef,
  );

  if (changedFiles.length === 0) {
    console.log(
      `No changed frontend TypeScript files found against ${options.baseRef}; skipped scoped type-check.`,
    );
    console.log('Use npm run type-check:all to inspect the full repo-wide baseline.');
    return;
  }

  console.log(
    `Checking ${changedFiles.length} changed frontend TypeScript file(s) against ${options.baseRef}:`,
  );
  for (const filePath of changedFiles) {
    console.log(`- ${filePath}`);
  }

  const result = runTypeScriptCompiler();
  if (result.error) {
    throw result.error;
  }

  const compilerOutput = `${result.stdout || ''}${result.stderr || ''}`;
  if (result.status === 0) {
    console.log('TypeScript compiler reported no diagnostics.');
    return;
  }

  const {
    changedDiagnostics,
    existingDiagnostics,
    globalDiagnostics,
  } = filterDiagnosticsForChangedFiles(compilerOutput, changedFiles);

  const blockingDiagnostics = [...globalDiagnostics, ...changedDiagnostics];
  if (blockingDiagnostics.length > 0) {
    console.error(
      `TypeScript found ${blockingDiagnostics.length} diagnostic(s) in changed frontend files.`,
    );
    console.error(blockingDiagnostics.join('\n'));
    process.exit(result.status || 1);
  }

  console.log(
    `TypeScript reported ${existingDiagnostics.length} existing repo-wide diagnostic(s), but none belong to changed frontend TypeScript files.`,
  );
  console.log('Use npm run type-check:all to inspect the full repo-wide baseline.');
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
