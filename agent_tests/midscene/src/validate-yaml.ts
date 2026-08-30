import path from 'node:path'

import { collectWorkflowDocument } from '@midscene/test'
import { discoverTestFiles, loadTestProject } from '@midscene/test/config'

const packageRoot = process.cwd()
const loaded = await loadTestProject(path.join(packageRoot, 'midscene.config.ts'))
const project = loaded.projects[0]
if (!project) throw new Error('Midscene configuration did not define a project')
const files = discoverTestFiles(packageRoot, { include: ['cases/*.yaml'] })
if (files.length !== 4) throw new Error(`expected exactly four YAML journeys, found ${files.length}`)

const validated = files.map((absolutePath) => {
  const sourcePath = path.relative(packageRoot, absolutePath).split(path.sep).join('/')
  const document = collectWorkflowDocument({
    projectId: project.projectId,
    projectName: project.name,
    sourcePath,
    absolutePath,
  }, {
    resolveNode: (name) => loaded.resolveNode(name),
    variables: project.variables,
    env: process.env,
  })
  if (document.cases.length !== 1) throw new Error(`${sourcePath} must contain exactly one case`)
  return { sourcePath, caseName: document.cases[0]?.definition.name, steps: document.cases[0]?.definition.steps.length }
})

process.stdout.write(`${JSON.stringify({ validated }, null, 2)}\n`)
