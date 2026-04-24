#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function usage() {
  console.log(`Usage: npm run type-check:changed -- [options]

Runs the normal frontend TypeScript check, then treats existing repo-wide
TypeScript debt as non-blocking when none of the reported errors are in files
changed by this branch.

Options:
  --base REF                Git ref to compare against. Default: origin/main.
  --frontend-root PATH      Frontend root. Default: current directory when it is
                            the frontend package, otherwise this script's parent.
  --tsc-output-file PATH    Testing/debug override: read tsc output from a file.
  --tsc-exit-code CODE      Testing/debug override used with --tsc-output-file.
  --help                    Show this help.
`);
}

function parseArgs(argv) {
  const options = {
    base: process.env.FRONTEND_TYPECHECK_BASE || 'origin/main',
    frontendRoot: '',
    tscOutputFile: '',
    tscExitCode: 1,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      usage();
      process.exit(0);
    }
    if (arg === '--base') {
      options.base = argv[i + 1] || '';
      i += 1;
      continue;
    }
    if (arg === '--frontend-root') {
      options.frontendRoot = argv[i + 1] || '';
      i += 1;
      continue;
    }
    if (arg === '--tsc-output-file') {
      options.tscOutputFile = argv[i + 1] || '';
      i += 1;
      continue;
    }
    if (arg === '--tsc-exit-code') {
      options.tscExitCode = Number.parseInt(argv[i + 1] || '1', 10);
      i += 1;
      continue;
    }
    console.error(`Unknown option: ${arg}`);
    usage();
    process.exit(2);
  }

  if (!options.base) {
    console.error('--base must not be empty.');
    process.exit(2);
  }
  if (!Number.isInteger(options.tscExitCode)) {
    console.error('--tsc-exit-code must be an integer.');
    process.exit(2);
  }

  return options;
}

function commandResult(command, args, cwd, options = {}) {
  return spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    maxBuffer: 50 * 1024 * 1024,
    ...options,
  });
}

function commandText(command, args, cwd) {
  const result = commandResult(command, args, cwd);
  if (result.status !== 0) {
    const detail = [result.stdout, result.stderr].filter(Boolean).join('\n').trim();
    throw new Error(`${command} ${args.join(' ')} failed${detail ? `:\n${detail}` : ''}`);
  }
  return result.stdout.trim();
}

function chooseFrontendRoot(requestedRoot) {
  if (requestedRoot) {
    return path.resolve(requestedRoot);
  }
  const cwd = process.cwd();
  const packageJson = path.join(cwd, 'package.json');
  if (fs.existsSync(packageJson)) {
    try {
      const packageData = JSON.parse(fs.readFileSync(packageJson, 'utf8'));
      if (packageData.name === 'ai-curation-frontend') {
        return cwd;
      }
    } catch {
      // Fall through to the script location when package.json is not parseable.
    }
  }
  return scriptRoot;
}

function toPosix(relativePath) {
  return relativePath.split(path.sep).join('/');
}

function normalizeErrorPath(rawPath, frontendRoot, repoRoot, frontendRepoPath) {
  let candidate = rawPath.trim();
  if (!candidate || candidate === '<anonymous>') {
    return '';
  }
  candidate = candidate.replace(/^file:\/\//, '');
  if (path.isAbsolute(candidate)) {
    candidate = path.relative(frontendRoot, candidate);
  } else if (candidate.startsWith(`${frontendRepoPath}/`)) {
    candidate = candidate.slice(frontendRepoPath.length + 1);
  } else if (candidate.startsWith(`${toPosix(path.relative(repoRoot, frontendRoot))}/`)) {
    candidate = candidate.slice(toPosix(path.relative(repoRoot, frontendRoot)).length + 1);
  }
  return toPosix(path.normalize(candidate));
}

function extractTscErrors(output, frontendRoot, repoRoot, frontendRepoPath) {
  const errors = [];
  for (const line of output.split(/\r?\n/)) {
    if (!line.includes('error TS')) {
      continue;
    }
    const fileMatch = line.match(/^(.+?)\((\d+),(\d+)\): error TS\d+:/);
    errors.push({
      file: fileMatch ? normalizeErrorPath(fileMatch[1], frontendRoot, repoRoot, frontendRepoPath) : '',
      line,
    });
  }
  return errors;
}

function getChangedFrontendFiles(repoRoot, frontendRoot, baseRef) {
  const frontendRepoPath = toPosix(path.relative(repoRoot, frontendRoot));
  const entries = new Set();
  const branchDiff = commandResult(
    'git',
    ['-C', repoRoot, 'diff', '--name-only', '--diff-filter=ACMR', `${baseRef}...HEAD`, '--', frontendRepoPath],
    repoRoot,
  );
  if (branchDiff.status !== 0) {
    const detail = [branchDiff.stdout, branchDiff.stderr].filter(Boolean).join('\n').trim();
    throw new Error(
      `Unable to compare frontend changes against ${baseRef}. Run "git fetch origin main" or pass --base with an available ref.${detail ? `\n${detail}` : ''}`,
    );
  }

  const stagedDiff = commandResult(
    'git',
    ['-C', repoRoot, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '--', frontendRepoPath],
    repoRoot,
  );
  const worktreeDiff = commandResult(
    'git',
    ['-C', repoRoot, 'diff', '--name-only', '--diff-filter=ACMR', '--', frontendRepoPath],
    repoRoot,
  );
  const untracked = commandResult(
    'git',
    ['-C', repoRoot, 'ls-files', '--others', '--exclude-standard', '--', frontendRepoPath],
    repoRoot,
  );

  for (const result of [branchDiff, stagedDiff, worktreeDiff, untracked]) {
    if (result.status !== 0) {
      continue;
    }
    for (const entry of result.stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)) {
      const frontendPath = entry.startsWith(`${frontendRepoPath}/`) ? entry.slice(frontendRepoPath.length + 1) : entry;
      entries.add(toPosix(path.normalize(frontendPath)));
    }
  }

  return [...entries].sort();
}

function runTypeCheck(frontendRoot, options) {
  if (options.tscOutputFile) {
    return {
      status: options.tscExitCode,
      output: fs.readFileSync(options.tscOutputFile, 'utf8'),
    };
  }

  const result = commandResult('npm', ['run', 'type-check', '--', '--pretty', 'false'], frontendRoot);
  return {
    status: result.status ?? 1,
    output: [result.stdout, result.stderr].filter(Boolean).join('\n'),
  };
}

function printExamples(title, errors) {
  if (errors.length === 0) {
    return;
  }
  console.log(title);
  for (const error of errors.slice(0, 12)) {
    console.log(error.line);
  }
  if (errors.length > 12) {
    console.log(`... ${errors.length - 12} more`);
  }
}

function printOutputExcerpt(title, output) {
  const lines = output.split(/\r?\n/).filter(Boolean).slice(0, 12);
  if (lines.length === 0) {
    return;
  }
  console.log(title);
  for (const line of lines) {
    console.log(line);
  }
}

const options = parseArgs(process.argv.slice(2));
const frontendRoot = chooseFrontendRoot(options.frontendRoot);

try {
  const repoRoot = commandText('git', ['-C', frontendRoot, 'rev-parse', '--show-toplevel'], frontendRoot);
  const frontendRepoPath = toPosix(path.relative(repoRoot, frontendRoot));
  const changedFiles = getChangedFrontendFiles(repoRoot, frontendRoot, options.base);
  const changedTypeScriptFiles = changedFiles.filter((file) => /^src\/.*\.[cm]?[tj]sx?$/.test(file));
  const globalTypeConfigChanged = changedFiles.some((file) => /^(tsconfig.*\.json|vite\.config\.[cm]?[tj]s)$/.test(file));
  const typeCheck = runTypeCheck(frontendRoot, options);

  console.log(`FRONTEND_TYPECHECK_BASE=${options.base}`);
  console.log(`FRONTEND_TYPECHECK_CHANGED_FILES=${changedFiles.length}`);
  console.log(`FRONTEND_TYPECHECK_CHANGED_TS_FILES=${changedTypeScriptFiles.length}`);

  if (typeCheck.status === 0) {
    console.log('FRONTEND_TYPECHECK_STATUS=passed');
    process.exit(0);
  }

  const errors = extractTscErrors(typeCheck.output, frontendRoot, repoRoot, frontendRepoPath);
  const changedSet = new Set(changedTypeScriptFiles);
  const changedErrors = errors.filter((error) => changedSet.has(error.file));
  const unscopedErrors = errors.filter((error) => !error.file);

  if (errors.length === 0) {
    console.log('FRONTEND_TYPECHECK_STATUS=failed_unscoped_errors');
    printOutputExcerpt('TypeScript exited nonzero without parseable error locations; treat this as actionable:', typeCheck.output);
    process.exit(typeCheck.status || 1);
  }

  if (globalTypeConfigChanged) {
    console.log('FRONTEND_TYPECHECK_STATUS=failed_global_config_changed');
    printExamples('TypeScript errors are actionable because a TypeScript/Vite config changed:', errors);
    process.exit(typeCheck.status || 1);
  }

  if (unscopedErrors.length > 0) {
    console.log('FRONTEND_TYPECHECK_STATUS=failed_unscoped_errors');
    printExamples('TypeScript emitted errors without file paths; treat these as actionable:', unscopedErrors);
    process.exit(typeCheck.status || 1);
  }

  if (changedErrors.length > 0) {
    console.log('FRONTEND_TYPECHECK_STATUS=failed_changed_files');
    printExamples('TypeScript errors in changed frontend files:', changedErrors);
    process.exit(typeCheck.status || 1);
  }

  console.log('FRONTEND_TYPECHECK_STATUS=baseline_only');
  console.log(`FRONTEND_TYPECHECK_BASELINE_ERRORS=${errors.length}`);
  printExamples('Existing TypeScript errors outside changed frontend files:', errors);
  process.exit(0);
} catch (error) {
  console.error('FRONTEND_TYPECHECK_STATUS=error');
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
}
