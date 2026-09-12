# Flow Builder unsaved reminder

Applying step settings changes the draft. The warning above the canvas remains until Save confirms the current draft. Failed saves and edits made during a pending save retain the warning. Untouched new flows, loaded saved flows and read-only flows do not show it.

The baseline at `5ee3629` contains an internal unsaved flag but no visible draft reminder or local-storage restore path in FlowBuilder. This change preserves the existing persistence behavior and does not claim that drafts survive browser reloads.

## Visual evidence

Screenshots use the real FlowBuilder, React Flow, NodePanel and application themes in a temporary static Vite harness with mocked flow/catalog/metadata HTTP responses. The fixture is a private one-step flow named “Reagent extraction”; the palette intentionally has no catalog entries. Chromium uses DejaVu Sans because the VM does not have the app's webfont installed. This is UI evidence, not a live account or extraction smoke.

| Viewport | Before Apply reminder change | After Apply reminder change |
| --- | --- | --- |
| 1440 × 900, dark | [Before](before-1440-dark.png) | [After](after-1440-dark.png) |
| 768 × 900, dark | [Before](before-768-dark.png) | [After](after-768-dark.png) |
| 1440 × 900, light | [Before](before-1440-light.png) | [After](after-1440-light.png) |
| 768 × 900, light | [Before](before-768-light.png) | [After](after-768-light.png) |
| 600 × 900 | — | [Dark](after-600-dark.png), [light](after-600-light.png) |

Browser-driven verification in both themes at all three widths:

1. Open the saved flow, select Initial Instructions and edit Task instructions.
2. Click Apply, then close the narrow settings drawer to return to the canvas.
3. Check the reminder appears above the canvas without overlaying its controls. At 600px, the explanatory sentence wraps and the row grows from 69px to 89px without horizontal overflow.
4. Click Save with a mocked HTTP 500 response; the reminder remains.
5. Focus the canvas and press Ctrl+S with a successful response; the reminder clears.

The reminder has a warning icon and explicit text, a single persistent polite atomic status region, no interactive elements and no focus movement. Its text/background contrast is 14.87:1 in dark mode (`#ffe2b7` / `#191207`) and 8.74:1 in light mode (`#663c00` / `#fff4e5`), computed from Chromium's rendered colors. Screen-reader speech was not manually audited.

## Automated validation

- `cd frontend && npm run test -- --run src/components/AgentStudio/FlowBuilder/FlowBuilder.test.tsx src/components/AgentStudio/FlowBuilder/NodePanel/NodePanel.test.tsx`: 64 tests passed, including clean/new/read-only state, Apply, failed Save, keyboard Save and edits during a pending Save.
- `cd frontend && npm run type-check:changed -- --base origin/main`: `baseline_only`, 19 pre-existing errors outside changed files.
- Broad clean-checkout coverage belongs to the GitHub Actions Frontend Tests job; no backend behavior changed.

The temporary harness and its build configuration were removed after verification. Browser libraries and fonts were unpacked under `/tmp`; repository dependencies were unchanged.
