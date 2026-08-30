import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { it } from 'node:test'

it('documents every smoke environment setting in .env.example', async () => {
  const source = await readFile(path.resolve(process.cwd(), 'src/config.ts'), 'utf8')
  const envExample = await readFile(path.resolve(process.cwd(), '../../.env.example'), 'utf8')
  const names = [...source.matchAll(/(?:readBoolean|readInteger|readNonEmpty)\(env,\s*'([A-Z][A-Z0-9_]+)'/g)]
    .map((match) => match[1]!)
  assert.ok(names.length > 20)
  for (const name of new Set(names)) {
    assert.match(envExample, new RegExp(`^${name}=`, 'm'), `${name} is missing from .env.example`)
  }
})
