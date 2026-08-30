import { createMidsceneNodes } from '@midscene/test/midscene'
import { createPlaywrightNodes } from '@midscene/test/playwright'
import { defineTestProject } from '@midscene/test/config'

import { loadConfig } from './src/config.js'
import { smokeNodes } from './src/nodes.js'
import { smokeAgentProvider, smokeProjectSetup } from './src/setup.js'
import type { SmokeContext } from './src/smoke-context.js'

const config = loadConfig(process.env, { cwd: process.cwd(), requireSecrets: false })

const variables = {
  appUrl: config.appUrl,
  runPrefix: config.runPrefix,
  createFlowName: `${config.runPrefix}-create-connect-save`,
  editFlowName: `${config.runPrefix}-edit-rewire`,
  runFlowName: `${config.runPrefix}-run-saved-flow`,
  uploadFilename: `${config.runPrefix}-sample-fly-publication.pdf`,
  createInstructions: 'Read the active publication, extract its focus genes, and save the result as JSON.',
  originalEditInstructions: 'Read the active publication, extract its focus genes, and save the result as JSON.',
  editedInstructions: 'Read the active publication, extract only experimentally supported genes, and save the result as CSV.',
  runInstructions: 'Extract only crb/Crumbs from the active publication with one verified evidence record, then save a compact JSON file preserving that evidence ID.',
  chatQuestion: 'What genes are the focus of the publication?',
} as const

export default defineTestProject<SmokeContext>({
  projects: [{
    name: 'local-curator-ui',
    platform: 'web',
    setup: smokeProjectSetup,
    files: { include: config.cases.map((caseName) => `cases/${caseName}.yaml`) },
    ...(config.tags.length > 0 ? { tags: { include: config.tags } } : {}),
    retry: config.caseRetryCount,
    variables,
  }],
  test: {
    maxConcurrency: config.maxConcurrency,
    bail: 0,
    testTimeout: config.testTimeoutMs,
  },
  output: { reportDir: `${config.runDir}/test-runner-reports` },
  nodes: [
    ...createPlaywrightNodes<SmokeContext>({
      getPage: ({ context }) => context.page,
      getBaseUrl: ({ context }) => context.config.appUrl,
    }),
    ...createMidsceneNodes<SmokeContext>({ agentProvider: smokeAgentProvider, includeLaunch: false }),
    ...smokeNodes,
  ],
})
