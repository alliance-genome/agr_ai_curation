# Agent Studio guide

Open **Agent Studio** from the top navigation to browse agents, create your own, or build a flow. The **Agents**, **Flows**, and **Agent Workshop** tabs share an **AI Chat** panel on the right.

## Work with AI Chat

Describe the curation task in your own terms. For example:

> Help me extract the fly stocks used in a paper. I need each stock's name and its source when reported. Walk me through creating the agent and a CSV flow.

AI Chat can inspect the current agent or flow, explain settings, and propose edits. It can help with:

- Agent names, descriptions, icons, models, reasoning levels, sharing, and group access.
- Main prompts, group instructions, tools, and output choices.
- Custom item types, details, parts, answer formats, choices, inclusion rules, and extraction instructions.
- Compatible built-in or custom validators attached to individual details or parts.
- Flow steps, connections, validation settings, and output columns or instructions.

It can also inspect available agent capabilities, selected validator prompts, and relevant read-only application information to explain a restriction. Available tools and your access still limit what it can do. Ask it to inspect a capability when unsure, rather than assuming that any agent or validator can be used for any task.

### Review, Apply, and Save

AI Chat prepares a proposal for your review. Read the highlighted **Changes to your draft** summary and any warnings about removals or validation changes. **Technical details** is collapsed by default; expand it for the full configuration. Clicking outside the dialog or pressing Escape does not dismiss the proposal. Choose Apply changes or Cancel.

**Apply changes** updates the open draft. The button shows progress while the proposal is being validated. **Cancel** dismisses the proposal. After a successful Apply, AI Chat can continue the conversation using the updated draft. Saving a Workshop agent during the conversation can also trigger the next step once the saved settings have loaded.

**Save** is separate: it saves the agent or flow to your account. An AI proposal, a green Apply confirmation, or a completed wizard does not mean you have saved it.

Manual edits are included when you next send a message. If you change the draft while an older proposal is pending, it may need a fresh review. Ask AI Chat to refresh its proposal. You can undo the last applied AI change while the draft still matches that change.

### Stop a response

While AI Chat is preparing, using tools, or writing, choose **Stop** beside the message box. If a review dialog is open while the response is still running, it also has a Stop button. The button shows **Stopping…** until the run ends.

The conversation keeps the partial answer, and you can send another message to narrow or redirect the request. Stop does not undo changes you already applied or saved. If stopping fails, the chat tells you and lets you retry.

### Start a fresh conversation

Choose **New chat** at the top of AI Chat to reset the conversation. This keeps the agent or flow you are editing. The new conversation can read that current editor context, but does not carry over the earlier discussion. Finish reviewing any pending proposal before resetting if you still need it.

### Make room for the editor

- Use **Hide AI Chat** and **Show AI Chat**, or **Ctrl+.** / **Cmd+.**, to toggle the panel. An orange dot indicates a reply arrived while it was hidden.
- Drag the divider to resize the panel.
- On a narrow window, use the **AI Chat** button to open a sheet. Close it with **Escape**, the close button, or a click outside it.

Actions such as **Discuss with AI Chat** and **Verify with AI Chat** open the panel when needed.

## Agents: inspect an existing agent

Use the agent list and its filters to find an extractor, validator, output formatter, or accessible custom agent. The available list depends on installed packages and your access; see [available agents](AVAILABLE_AGENTS.md) for the main types.

The agent view lets you inspect its base prompt, group-specific rules, combined prompt, tools, and output information. Click a tool name to read what it does and which inputs it accepts.

For packaged extraction, the output information describes the supported objects and fields, validation, and export or submission support. You may see the term **domain envelope**: this is the saved structured record, with evidence and validation findings. A review table displays selected fields from it.

A validator listed as under development is not an active validation step. Also distinguish a validator's configuration from its findings after a run. Ask AI Chat which validators actually run and what unresolved answers mean for your task.

**Clone to Workshop** creates an editable draft based on an agent. Changing its prompt does not change a packaged biological format. If you need fields that format does not support, use a custom output structure instead.

## Agent Workshop: create or edit an agent

### Choose a starting point

Open **Agent Workshop** and choose **New** if an agent is already open.

| Choice | Use it when… |
|--------|--------------|
| **Custom data extraction** | You want to define your own item type and details. This opens the extraction wizard directly. |
| **From a template** | An existing agent is a useful starting point for your instructions and tools. |
| **From scratch** | You want to configure the agent yourself. Built-in runtime rules still apply. |
| **Clone one of yours** | You want a copy of a saved agent to adapt. |

For custom extraction, follow **Name the item type → Choose its details → Review & finish**. The wizard supports one item type per agent. When finished, review the rest of the agent in Setup and save it. The [custom output guide](CUSTOM_OUTPUT_STRUCTURES.md) walks through the field editor and validation.

### Setup

Review these settings, manually or with AI Chat:

| Setting | Purpose |
|---------|---------|
| **Starting point** | The template or saved agent used to create this draft |
| **Identity** | Icon, agent name, and a short description for people choosing it later |
| **Model** | The model and supported reasoning level used when the agent runs |
| **Sharing** | Who can see the agent and which groups may run it |
| **Output** | Whether to produce structured extraction, and which structure to use |

New extraction drafts default to **GPT-6 Astra with low reasoning**. Studio AI Chat uses Astra with medium reasoning; validator agents retain their Terra settings. Existing saved agents and flow revisions retain their saved choices. Read **Model guidance** or choose **Ask AI Chat which model fits** if you want to change the model.

**Visibility** and **Available to groups** serve different purposes: sharing controls discovery, while groups restrict execution. A restricted template or clone source may let you narrow group access without widening it.

Under **Output**, read the explanation below the selected mode. Custom Output Structure defines consistent fields; Flexible extraction allows fields to vary; Packaged domain format uses an existing biological structure. [Compare the modes](CUSTOM_OUTPUT_STRUCTURES.md#choose-an-output-mode) before changing one.

For custom output, Setup includes a read-only details table. Choose **Add details to collect** or **Edit details to collect** above the table. This opens the same structure in the editor, where you define what the AI should collect—not actual answers from a paper. **Back to Setup** returns to the agent settings.

### Prompt

The prompt view separates locked instruction layers from **Your prompt**, which you can edit. The built-in and output-structure instructions supply rules the agent must follow. Your prompt supplies task guidance. **Reset to template** restores the template text for the editable prompt.

Use **Group-specific instructions** for conventions that apply to a particular curator group. You can edit a group's text or reset it to the template. **Add group instructions at runtime** controls whether those instructions are included when the agent runs.

Custom item-type guidance and detail instructions are also passed to the extraction AI. Use them for record boundaries and field-specific rules, in addition to the main prompt. See [where to put instructions](BEST_PRACTICES.md#put-instructions-where-they-apply).

### Tools

The Tools table shows attached tools and their purposes. Choose **Add tools**, search or filter the library, select tools, then choose **Attach N tools**. A tool disabled by policy cannot be attached; its entry explains why.

Extraction and validation use different tools. Extractors collect paper evidence; validators resolve supported values against databases or ontologies. Attaching a lookup tool is not a substitute for configuring a field validator.

If a needed tool is missing, choose **New request** to describe it for the development team. **Ask AI Chat to draft a request** can help write the request. The request list shows its status.

### Save and manage versions

The header provides **Open**, **New**, **Save**, and a **More** menu with **Save as**, **Manage agents**, and **Delete agent**.

**Save** opens a dialog summarizing the changes and allows a version note. **Save as** creates a separate copy. The **Versions** section lists saved versions; **Revert** creates a new version from an older one rather than deleting history.

Saving an agent or custom structure creates or selects saved revisions. Existing flow steps keep the revisions they were configured to use. Review and explicitly update a flow when you want it to use a changed agent.

### Keep and recover unsaved work

Browser Back follows Workshop sections, wizard stages, and detail pages while keeping edits. Switching between Studio tabs also keeps the current draft.

Workshop and Flow Builder keep recovery drafts in this browser for your signed-in account, including unfinished flow-step instructions. After a reload or return, choose **Resume draft** or **Discard draft**. Local recovery is not an account save and does not transfer to another device. Clearing browser storage can remove it. If storage is unavailable, keep the page open and use **Save**.

Recovered flows open as unsaved copies. A recovered agent whose saved source has changed or is unavailable may also open as a new copy; a shared-agent clone needs access to its source. Review before saving. These copies avoid overwriting saved work during recovery.

Starting or opening different work can ask how to handle unsaved edits. Read the prompt before discarding them. If another tab owns a recovery draft, follow the warning rather than assuming both tabs are keeping independent recovery copies.

## Flows: use agents together

A flow combines initial task instructions, extraction, any additional validation, and at least one output. Use **Help build a flow** or ask AI Chat to work through it one decision at a time. You can also ask for a complete proposal when you already know the configuration.

If a suitable pre-made agent exists, inspect its fields first. If you need a custom one, AI Chat can help build it in Workshop while keeping the flow context.

When working with AI Chat on an agent for a flow, save the agent and return to the **Flows** tab to continue the saved-agent handoff automatically. It runs once after any current reply finishes. You can also choose **Review in Flow** in the saved-agent notice. Studio returns to the preserved flow and asks AI Chat to propose how to use the saved agent. Review and Apply that proposal, then save the flow separately. Changed drafts or unavailable agents may require a fresh review.

Use **Verify with AI Chat** to discuss structure, missing connections, or validation choices. This is assistance before running, not an extraction test or a guarantee of correct results. See [curation flows](CURATION_FLOWS.md) for connections, output instructions, and running a saved flow.

## Understand a result or report a problem

In the main chat, open a response's **three-dot menu (⋮)** and choose **Open in Agent Studio**. Ask about the specific result, for example:

> Why did this allele remain unresolved? Which information did the validator receive?

When available, AI Chat can inspect the linked run's prompts, tool activity, evidence, and validation timeline. Individual failed lookup attempts can precede a successful result, so ask about the final finding as well as the activity log.

Validation findings identify the affected record or field. Read unresolved or ambiguous results before export. A curator override or waiver is available only where the data type's policy allows it. A successful lookup does not, by itself, make a custom record ready for Alliance submission.

Choose **Send feedback** in the AI Chat header for **AI-assisted** feedback or a **Manual** report. Include the expected behavior, the observed result, and any relevant group convention. For a main-chat extraction problem, the response's feedback action keeps the report linked to that interaction.

## Common questions

### Can AI Chat change my custom fields and validators?

Yes. It can propose edits to details, parts, instructions, inclusion rules, and compatible validator attachments. Review and Apply its proposal, then Save. It cannot attach a validator that lacks a supported contract or that you cannot access.

### Does editing the table enter answers from a paper?

No. The Workshop table defines the questions the extractor will answer. Run the saved agent or flow on a paper to obtain records, then review those results.

### How do prompts layer together?

The agent's prompt, group instructions, flow instructions, and custom field guidance all contribute. Locked runtime and output rules still apply. See [how prompts layer together](CURATION_FLOWS.md#how-prompts-layer-together).

### What's the difference between AI Chat and the main chat?

Studio AI Chat helps configure and explain agents and flows. The main chat is where you ask about papers, run flows, and receive extraction or lookup results.
