# Curation flows guide

A flow saves a task and the agents used to carry it out. Use it to repeat an extraction across papers with the same instructions, validation choices, and output layout.

Open **Agent Studio → Flows**. The editor contains an agent palette, a canvas, and a settings panel for the selected step. **AI Chat** is on the right and can be hidden or resized.

## Build with AI Chat

Choose **Help build a flow**, or describe your task:

> Help me build a flow for Drosophila stocks. Let's agree on the extraction instructions first, then the details, validation, and CSV columns.

AI Chat can work through the decisions one at a time. Start with the task, then decide whether a pre-made extractor has the fields you need. If it does not, create a custom agent in [Agent Workshop](AGENT_STUDIO.md#agent-workshop-create-or-edit-an-agent). AI Chat can help edit that agent while preserving the flow you are building.

Review each proposal before choosing **Apply changes**. Apply changes the draft; **Save** saves the flow. A progress indicator appears while a proposal is being validated. After a successful Apply, AI Chat can continue with the next decision. If a proposal conflicts with newer edits, ask for a refreshed proposal.

## Build or edit on the canvas

### 1. Set Initial Instructions

Use one **Initial Instructions** node and write the overall task. For example:

> Extract each distinct fly stock used in this paper. Include stocks from any source and combine repeated mentions of the same stock. Exclude background-only mentions.

### 2. Choose an extractor

Click or drag an agent from the palette onto the canvas. Search the palette by name, description, or tools. Accessible saved custom agents appear alongside package agents.

A packaged extractor has predefined fields. Changing its prompt does not add fields to that format. For a custom set of details, use an agent with **Custom Output Structure**. You usually do not need a separate General PDF Extraction step before a domain extractor: the extractor can read the paper itself.

### 3. Connect the steps and outputs

Drag between the nodes' connection handles. Non-output steps run in a single ordered chain. Output connections select the saved results to format; they are separate from that chain.

For a simple flow, connect Initial Instructions to your extractor, then connect the extractor to a CSV formatter or Chat Output. An output can have several selected source steps, and a source can feed more than one output. For example, the same extraction can produce a chat summary and a TSV file.

Every output needs a source. Attach it directly to each extraction or validation result it should include. Each output runs once after its selected sources finish. Adding output connections does not enable parallel extraction branches.

### 4. Configure each step

Select a node to open its settings panel. Drag the panel's edge to resize it. On narrow screens it opens as a drawer.

**Apply** in this panel keeps the step's edits in the flow draft. **Cancel** restores the panel's prior settings. Selecting another step with unapplied edits prompts you to apply, discard, or keep editing. Use the panel menu for **Delete step**.

#### Instructions for this step

Add guidance specific to this flow's use of the agent. For example, restrict extraction to a particular organism or experimental question. These instructions do not change the agent's saved prompt for other uses or override its locked output and runtime rules.

#### Automatic checks

The current interface labels packaged automatic validation as **Automatic checks**. The summary shows how many run and how many always run. **Adjust optional checks** exposes those you can disable for this flow. The information button explains the affected fields and the consequence of disabling each one.

Required or locked validators cannot be turned off. Under-development validators do not run. If an explicit custom validation step replaces an automatic validator, the panel identifies that relationship.

For a custom output structure, attach compatible validators to individual details or parts in Workshop. See [field validation](CUSTOM_OUTPUT_STRUCTURES.md#attach-a-validator-to-a-detail-or-part). A field without a semantic validator still has its configured structure rules enforced.

#### Custom validator steps

When supported, a custom validator node can replace or supplement validation for an extraction step. Review which source and validation it targets. Its steering prompt adds guidance for this use of the validator. Ask AI Chat to inspect the available options; naming a field in a prompt alone does not create a compatible validator attachment.

#### Output steps

Choose **Chat Output**, CSV, TSV, or JSON. A flow needs an output even if you do not want a downloadable file.

For CSV, TSV, and JSON, choose how to build the file:

- **Use selected fields** fixes the columns across runs. Open **Choose output fields**, select details from the connected agents, rename or reorder the columns, then choose **Use these fields**. Apply the output-step changes and save the flow.
- **Let AI arrange the output** uses your **Output instructions** to choose a layout from the available results. This is useful for exploratory work; columns can vary between runs.

**Export structured data directly — faster** skips the extra AI call and copies your selected fields into the file. Turn it on only when you want the values unchanged. Agent prompts, group prompts and output instructions do not run in this mode; your instruction text is kept and becomes available again when you turn direct export off. Renaming and reordering columns are supported; combining or rewriting values is not.

The field picker works with saved custom structures and packaged field declarations. It shows each connected source separately. For a stock extractor, you might select **Stock name**, then the **Supplier name** and **Catalog number** parts of Source. Select the whole Source answer instead if you want to keep its parts together.

JSON keeps selected groups as objects and lists as arrays. CSV and TSV put whole groups and lists in a cell as JSON; selecting individual parts gives them separate columns. Records from different source steps remain separate rows, with blanks in columns that belong to another source. They are not joined into a new biological record.

Missing answers are blank in CSV/TSV and null in JSON. Selecting a column does **not** make the extractor require an answer. If a run finds no items, CSV/TSV still include the selected headers and JSON contains an empty list. If a source structure changes, review the field selection again before running.

For Chat Output, use **Output instructions** to describe the summary or table. File output steps also offer filename choices and a filename preview.

**Need help with your output? Chat with AI** opens help for that exact step while keeping unapplied edits. AI can help choose fields, rename or reorder columns, and propose the same settings for your review. Selected fields control the file layout; instructions cannot add columns or invent missing values.

A formatter arranges collected information. If you need a new biological field, add it to a suitable custom extractor first; putting its name in the CSV instructions is not enough.

#### Output variable and agent information

**Output variable** names the step's saved result. The default is usually sufficient; custom names use letters, numbers, and underscores.

**About this agent** links to the agent's **Guide**, **Envelope**, and **Prompts**. Use these to inspect what it collects and validates without changing the step.

### 5. Review and save

Use **Verify with AI Chat** for help finding configuration issues. The editor also validates the flow and reports missing instructions, invalid connections, or other configuration errors. AI review does not execute the flow or validate its extracted answers.

Choose **Save**, give the flow a descriptive name, and add a description if useful. For example: “Drosophila stocks to CSV.”

## How prompts layer together

| Instructions | Purpose |
|--------------|---------|
| Initial Instructions | The overall task for the flow run |
| Agent prompt | Reusable task guidance from the selected agent |
| Group-specific instructions | Conventions for active curator groups |
| Instructions for this step | Guidance specific to this use of the agent |
| Custom item-type and detail instructions | Record boundaries and how to collect each custom answer |
| Output instructions | Presentation of the selected saved results |

The runtime also supplies document context and locked output, tool, and evidence rules. Step instructions can narrow editable task guidance, but cannot override those locked rules, add undeclared fields, or make an unsupported submission format valid.

Avoid conflicting instructions. For example, “only extract C. elegans genes” narrows an agent's broader extraction task. Asking a fixed expression-pattern format for extra fold-change fields needs a structure change, not just a stronger prompt.

Each step receives its task and document context. Structured results from earlier steps are saved for review and output; you do not need to paste those results into later prompts or invent variable templates to pass them along.

## Run a saved flow

1. Return to the main chat and open the paper you want to process.
2. Open **Tools** in the right panel.
3. Find the saved flow and choose **Run**.
4. Review the chat output, downloadable files, and any validation findings.

You can also choose a saved flow as your **Tools → Chat default** when you want ordinary chat requests to use it. Return to **Automatic** for general routing. RGD curators should follow the [RGD paper-review guide](RGD_GO_DISEASE_PAPER_REVIEW.md) for its recipes and request fields.

Test on one representative paper before using [batch processing](BATCH_PROCESSING.md). Batch processing requires PDF extraction and a supported file output or curation handoff; chat-only output is insufficient.

## Review results and export

| Output | Use |
|--------|-----|
| Chat | Read a summary or table and ask follow-up questions |
| CSV | Open a table in spreadsheet software |
| TSV | Use a tab-separated table in downstream tools |
| JSON | Preserve structured or grouped information |

Download files from their cards in the chat. Keep copies of results you need for later work.

Packaged extraction may also provide a curation review session. Its tables display saved records with evidence and validation findings. Export or submission previews check the current records, required fields, findings, and the data type's readiness rules. Resolve the stated blockers before final actions. Overrides are available only where the relevant policy allows them.

Neither a downloaded spreadsheet nor a confirmed identifier establishes submission readiness. Custom output records are not automatically Alliance submission objects.

## Example workflows

### Gene expression to CSV

Connect **Initial Instructions → Gene Expression Extractor**, then attach **CSV File Formatter** to the extractor.

Task: “Extract expression patterns for C. elegans, including anatomical locations and developmental stages.” Inspect the extractor's automatic validation before adding separate validator steps; the relevant validation may already run.

### Custom stocks to TSV and chat

Create and save a custom stock extractor with a required **Stock name** and optional **Source**. Connect **Initial Instructions → your stock extractor**. Attach both **TSV File Formatter** and **Chat Output** directly to that extractor.

Use **Choose output fields** in the TSV step to select the stock and source columns, plus supporting evidence IDs if needed. Ask Chat Output for a short summary of the stocks and any missing sources. If no compatible source validator is available, review source associations against the paper.

## Manage flows and unsaved drafts

Use **File → Open Flow...** to open saved work, **Save** to keep changes, and **Manage Flows...** to rename or delete flows. **New Flow** starts another flow. Read the unsaved-changes prompt before discarding the current draft.

Studio keeps the current flow while you switch tabs. It also retains a recovery draft in this browser for your account, including unfinished step-panel instructions. After a reload or return, choose **Resume draft** or **Discard draft**. Recovered flows open as unsaved copies so you can review them before saving.

Local recovery is not an account save. It does not transfer between devices and may be removed when browser storage is cleared. If recovery storage fails, keep the editor open and Save. See [draft recovery](AGENT_STUDIO.md#keep-and-recover-unsaved-work) for details.

Saved flow steps retain their selected agent revisions. Saving a new version in Workshop does not automatically upgrade existing flows. Explicitly select the changed agent revision and review the flow before rerunning it.

## Troubleshooting

**Flow cannot start because a step is unavailable:** The flow stops before running any agents; it does not skip required work and continue to the output. If a step needs a paper, open **Documents** in the top navigation and load the paper into chat. In Flow Builder, check that each step uses an available agent. Validators marked as attachment-only belong on an extraction step as validation attachments, not in the ordinary execution chain. Flows whose agents do not require a document can still run without one.

**The flow has no visible result.** Confirm it has a connected output, and that the output selects the intended source steps.

**A CSV column is missing or empty.** Check whether the extractor collected that field, then inspect the output instructions and any saved column layout. Ask AI Chat to compare them.

**Apply reports that the draft changed.** Your newer edits are preserved. Ask AI Chat to read the current draft and prepare a fresh proposal.

**A validator is attached but an answer is unresolved.** Inspect the finding. The input may be ambiguous, missing needed context, absent from the database, or affected by a lookup failure. An attachment is configuration, not a successful result.

**An updated agent did not change my flow.** The flow still uses its saved revision. Review and update the selected revision explicitly.

For an unexpected run result, use the response's **three-dot menu (⋮)** to send feedback or **Open in Agent Studio**. Include the expected result and a specific example from the paper.


## Keep your usual flows beside chat

The **Curation Flows** panel beside chat is your personal list of shortcuts.
Choose **Add flow** to search your saved flows and bring one into the list.
Drag a card by its dotted handle to change the order, or use **Move up** and
**Move down** in the card’s three-dot menu. With the handle focused, you can
also use the up and down arrow keys. Your selection and order are saved to
your account.

**Hide** removes a shortcut from this panel. It keeps the saved flow, its agents
and its history. Choose **Add flow** to bring it back. To edit or delete a flow,
open **Flows workspace**. Hiding or reordering a shortcut does not change your
chat routing choice or the flow itself.

Your current flows appear initially. Once you personalize the list, use
**Add flow** when you want another saved flow to appear there.
