# Curation Flows Guide

Curation Flows are guided supervisor conversations that run multiple AI agents in a saved order. You build them once, save them, and reuse them across documents.

> **Note:** Flows support **sequential (linear) runs** - each agent connects to the next in a chain. Each node can have only one outgoing connection.

## Why Use Curation Flows?

**Time Savings**
- Build a workflow once, reuse it many times
- Don't retype the same instructions for each document

**More Control**
- Define exactly which agents run and in what order
- Add custom instructions to each step in your workflow
- Fine-tune individual agents for your specific use case

**Custom Instructions at Every Step**
- In regular chat, you give one set of instructions to the whole system
- In flows, you can customize instructions for each agent individually
- Example: Tell the PDF agent to focus on methods sections, then tell the validation agent to only accept certain ontology types

**Domain Envelopes and Automatic Validation**
- Domain-pack extraction agents save their results as domain envelopes
- Flow Builder shows which curatable objects and field paths the extractor produces
- Active default validators attach automatically from domain-pack metadata
- Active validators can be skipped only when flow configuration replaces or supplements them with explicit validation
- Under-development validators are visible metadata and are not scheduled
- Extractors preserve paper-backed proposals and selector hints; validators own
  authoritative database/API/ontology resolution and materialized fields

**Repeatable Results**
- Same workflow = consistent extraction across documents
- Great for processing batches of similar papers

> **Tip:** Need to process multiple documents? See **[Batch Processing](BATCH_PROCESSING.md)** to run saved flows against multiple PDFs automatically.

## Accessing the Flow Builder

1. Click **"Agent Studio"** in the navigation bar
2. Select the **"Flows"** tab
3. The Flow Builder canvas appears on the right, with AI Chat on the left

## Flow Builder Interface

**AI Chat (Left Panel)**
Use AI Chat to discuss your flow - ask for help building it, troubleshooting
issues, understanding what each agent does, or checking which active validators
the domain-pack validation plan will schedule. When validator-agent IDs are
present, AI Chat can inspect those validator prompts and tools through Agent
Studio's existing prompt-inspection tools.

**Agent Palette (Left Panel)**
A searchable, collapsible list of available agents organized by category. Click or drag agents onto the canvas. Use the search box to filter agents by name, description, or tools.

**Canvas (Center/Right)**
The main workspace where you build your flow by adding agents and connecting them.

**Step Panel (Right)**
When you select a node, a panel opens beside the canvas with the settings that step owns: instructions, the optional automatic checks, and output options. Drag its left edge to resize it, or hide it to a narrow strip. On a narrow window it opens as a drawer over the canvas.

## Available Agents

### Input
| Agent | Description |
|-------|-------------|
| **Initial Instructions** | Starting point - define the task for your flow |

### PDF Extraction
| Agent | Description |
|-------|-------------|
| **PDF Extraction Agent** | Extracts text, tables, and data from PDF documents |
| **Gene Expression Extractor** | Extracts gene expression patterns from PDFs |

### Data Validation
| Agent | Description |
|-------|-------------|
| **Gene Validation Agent** | Validates gene identifiers against AGR database |
| **Allele Validation Agent** | Validates allele identifiers against AGR database |
| **Disease Ontology Agent** | Maps disease terms to DOID identifiers |
| **Chemical Ontology Agent** | Maps chemical names to ChEBI identifiers |
| **GO Term Lookup Agent** | Looks up Gene Ontology term definitions |
| **Gene GO Annotations Agent** | Retrieves existing GO annotations for genes |
| **Ortholog Lookup Agent** | Queries orthology relationships across species |
| **Ontology Term Resolver Agent** | Resolves exact CURIEs and typed ontology labels or synonyms to ontology terms |

### Output
| Agent | Description |
|-------|-------------|
| **Chat Output Agent** | Displays results in the chat for review |
| **CSV File Formatter** | Generates downloadable CSV files |
| **TSV File Formatter** | Generates downloadable TSV files |
| **JSON File Formatter** | Generates downloadable JSON files |

### My Custom Agents

If you've created custom agents in **Agent Workshop**, they appear here under "My Custom Agents". You can use them in flows just like system agents. See **[Agent Studio](AGENT_STUDIO.md)** for details on creating custom agents.

## Building a Flow

### Step 1: Add Agents to the Canvas

**Click to Add:** Find an agent in the Agent Palette and click it to add to the canvas.

**Drag and Drop:** Click and hold on an agent, drag it onto the canvas, and release.

### Step 2: Connect Agents

1. Hover over an agent node to see connection points (handles)
2. Click and drag from one handle to another agent's handle
3. Release to create the connection

**You can connect agents in any direction** - top to bottom, left to right, whatever makes sense for your workflow.

### Step 3: Configure Each Step

Click any node to open the **step panel** beside the canvas. The header shows the agent, its step number, and whether the step has unsaved changes or a configuration error. **Apply** saves your edits to the step, **Cancel** puts them back, and the menu in the header holds **Delete step**. If you click another node while edits are unsaved, the panel asks whether to apply them, discard them, or keep editing.

**Instructions for this step**

Add instructions for this step only. They are added to the agent's prompt with highest priority, so they override the agent's default behavior for this flow step. Example: "Focus only on gene expression data from the methods section."

**Automatic checks**

Extraction steps show what runs automatically on what the step extracts: a one-line summary such as "9 checks run on what this step extracts, 1 turned off for this flow", and how many of those checks always run. Checks that are blocking, or that the domain pack locks on, are counted but not listed, because you cannot turn them off.

Click **Adjust optional checks** to see one switch per check you may turn off for this flow. Each switch is one sentence in plain words, such as "Confirm the annotation type against the Annotation Type vocabulary". The info circle beside a switch opens a short explanation: what the check does, which fields it checks, what happens to those fields if you turn it off, and links to the validator's guide and the field in the Agents tab. All of that wording comes from the domain pack, so it matches what the Agents tab says.

If a custom validator step replaces one of the automatic checks, the summary says so, and that check no longer appears as a switch.

Under-development checks do not run and are not shown here. Current findings, lookup notes, and export or submission readiness are shown from the saved domain envelope after a run.

**Custom validator steps**

A custom validation agent placed after an extraction step shows which step it attaches to and which automatic check it replaces or adds to. Its **steering prompt** is added to the validator's prompt for this step only. Use it to name the envelope object, field, or question you want checked.

**Output steps**

A formatter step shows which step's results it formats, a switch to include the supporting evidence in the output, and, for file formatters, the file name choice: the paper's file name, a custom prefix, or the formatter's own name. An example file name is shown beneath the choice.

**Output variable**

Every step names its saved result for later steps and exports. The default name is fine for most flows. To rename it, open **Output variable** at the bottom of the panel. Names can contain letters, numbers, and underscores. Example: `validated_genes`

**About this agent**

The row at the bottom of the panel links to the agent in the Agents tab: its **Guide**, what it produces and checks in **Envelope**, and its **Prompts**. The panel itself holds only what the step owns.

**Step Context**

Each step receives the flow's Initial Instructions, the loaded document context,
the selected agent, and that node's instructions for the step. The runtime preserves
structured artifacts from earlier steps separately for review, export, and
follow-up lookup. Later step prompts do not use hidden previous-output text or
custom variable templates.

### Step 4: Verify with AI Chat

Before saving, click the **"Verify with AI Chat"** button. AI Chat will:
- Check your flow structure for issues
- Identify missing connections or problems
- Suggest improvements
- Confirm your flow is ready

This is especially valuable when building new flows.

### Step 5: Save Your Flow

1. Click the **"Save"** button
2. Enter a descriptive name (e.g., "C. elegans Expression to WBbt TSV")
3. Add an optional description
4. Click **"Save"**

## Flow Builder Toolbar

The Flow Builder toolbar provides quick access to common operations:

**File Menu**
- **New Flow** (Ctrl+N) - Start a new empty flow
- **Open Flow...** (Ctrl+O) - Open a previously saved flow
- **Manage Flows...** - Rename or delete saved flows
- **Save** (Ctrl+S) - Save the current flow
- **Delete Flow** - Remove the current flow

**Edit Menu**
- **Select All** (Ctrl+A) - Select all nodes on the canvas
- **Delete Selected** (Del) - Remove selected nodes

**Verify with AI Chat** - Appears when your flow has nodes. Sends the flow to AI Chat for structural review before running.

## Flow Validation

The Flow Builder validates your flow and shows error indicators when there are issues:

- **Missing task instructions** - The Initial Instructions node requires non-empty instructions
- **Parallel connections** - A node has more than one outgoing connection. Each node can connect to only one downstream step.
- **Duplicate Initial Instructions** - Only one Initial Instructions node is allowed per flow

Validation errors appear as a red banner under the step panel header when you select the affected node.

## How Prompts Layer Together

Each agent has multiple prompt layers that combine when the agent runs. Understanding these layers helps you write effective custom instructions and avoid conflicts.

```
Flow Custom Instructions   ← HIGHEST priority (prepended, overrides everything)
Base Prompt                ← Core agent behavior (from config/YAML)
Group-Specific Rules       ← Appended when your groups are active
Document Context           ← Auto-injected (document hierarchy, sections, abstract)
Output Schema              ← Auto-injected when structured output is configured
```

**Layer 1 — Base Prompt:** The core instructions that define the agent's role, mission, and workflow. You can view these in the Agent Browser on the Agents tab.

**Layer 2 — Group-Specific Rules:** Customizations for each curator group (WormBase, FlyBase, MGI, etc.). These are appended to the base prompt when your groups are active. You can view these on the Prompts tab in the Agent Browser; the step panel's **About this agent** row takes you there.

**Layer 3 — Flow Custom Instructions (highest priority):** Instructions you add to a step in the flow step panel. These are prepended to the agent's prompt and explicitly marked as highest priority — they override both the base prompt and group rules for that flow step.

**What this means in practice:**
- If the base prompt says "extract all genes" but your flow custom instructions say "only extract C. elegans genes," the flow instructions win.
- Group rules and the base prompt still apply for anything your flow instructions don't address.
- Each layer has its place: base prompts define core behavior, group rules add organism-specific conventions, and flow instructions give you fine-grained control for specific workflows.

> **Tip:** When writing flow custom instructions, you don't need to repeat what's already in the base prompt or group rules. Just add what's different or more specific for this particular workflow step.

## How Flows Execute

Understanding how flows run helps you build effective workflows:

1. **Initial Instructions** provide the starting task description and context
2. A supervisor agent receives all steps and executes them **sequentially** in the order defined by your connections
3. Each step runs with the flow task, document context, selected agent, and node custom instructions; prior step artifacts stay saved separately for review/export lookup
4. Output agents are attached to one or more structured extraction or validation steps. Each output agent runs once after all of its selected sources complete
5. Custom instructions for each step are applied with highest priority, overriding the agent's default behavior for that step (see [How Prompts Layer Together](#how-prompts-layer-together) above)
6. Domain-pack extraction steps save envelope objects and schedule automatic validation according to the node's validation attachments

**Important:** Output agents are branches, not ordinary steps in the sequential chain. Connect each output directly to every structured source it should include. A flow may produce more than one result—for example, a chat summary and a TSV file—from the same completed sources.

## Running a Flow

RGD curators using the package-owned paper-review recipes should follow the
task-specific **[RGD GO and Disease Paper Review](RGD_GO_DISEASE_PAPER_REVIEW.md)**
guide for upload, starter fields, saved Chat default selection, blocker review,
and result-reference follow-ups.

After building and saving your flow:

1. **Navigate to the main chat screen** (click "Home" in the navigation bar)
2. **Load a PDF document** if your flow uses PDF extraction agents
3. **Click the "Tools" tab** on the right panel
4. **Find your saved flow** in the list
5. **Click the "Run" button** next to your flow

The flow executes and results appear based on your output agent (chat message or downloadable file).

## Output Options

Flows can output results in different ways. Choose one or more output agents
that fit your needs. An output agent can combine several explicitly connected
structured extraction or validation results and runs once per flow. Its custom
instructions can shape presentation, such as column names, column order,
filters, sorting, or whether to show object, evidence, or validation rows.

### Chat Output Agent

Sends results directly to the chat interface.

**Use cases:**
- Quick review before generating a file
- Iterating on your flow to get the output right
- Discussing results with AI Chat in Agent Studio

### CSV File Formatter

Creates comma-separated value files from completed flow artifacts for spreadsheet applications.

**Use cases:**
- Import into Excel or Google Sheets
- Database import
- Sharing with collaborators

### TSV File Formatter

Creates tab-separated value files from completed flow artifacts, preferred by many databases.

**Use cases:**
- Database import
- AGR data submission
- Bioinformatics tools

### JSON File Formatter

Creates structured JSON files from completed flow artifacts that preserve complex nested data.

**Use cases:**
- Data with hierarchical structure
- Sharing with computational biologists

### Downloading Files

When a flow generates a file:
1. A download card appears in the chat
2. Click the download button to save the file
3. Files are available until the session ends

### Review, Export, and Submission Readiness

Domain-pack extraction can also create review sessions with envelope object
rows. The review table is a projection over the saved envelope. When you preview
export or direct submission, the system checks the expected envelope revision,
required fields, active validation findings, definition state, and adapter-owned
readiness policy.

If blockers appear, resolve the listed object/field issue before final export or
submission. Curator overrides only work when the domain-pack policy allows them
and a reason has been saved only when that specific policy requires one.

## Example Workflows

### Example 1: Gene Expression Extraction to CSV

**Goal:** Extract gene expression data from a paper and export to CSV

```
Initial Instructions → PDF Extraction Agent → Gene Expression Extractor → CSV File Formatter
```

**Instructions for Initial Instructions node:**
"Extract all gene expression data from this paper, including anatomical locations and developmental stages."

### Example 2: Ontology Term Resolution Pipeline

**Goal:** Extract expression data and resolve terms to official IDs

```
Initial Instructions -> PDF Extraction Agent -> Gene Expression Extractor -> Ontology Term Resolver Agent -> TSV File Formatter
```

**Instructions for Ontology Term Resolver Agent node:**
"Resolve all anatomy labels using WormBase provider-scoped anatomy lookup and all stage labels using WormBase provider-scoped life-stage lookup. Preserve unresolved or ambiguous candidates."

### Example 3: Full Pipeline with File Export

**Goal:** Extract expression data, validate terms, and export to TSV

```
Initial Instructions → PDF Extraction Agent → Gene Expression Extractor → Gene Validation Agent → TSV File Formatter
```

**Instructions for Initial Instructions node:**
"Extract gene expression data and validate all gene identifiers before export."

For domain-pack extractors, check the Gene Expression Extractor node before
running. Its automatic validation attachments may already include required
validators, so add a separate validation agent only when you need extra custom
checks or a curator-specific steering prompt.

## Managing Flows

### Loading Saved Flows

1. In the Flow Builder, use **File → Open Flow...** (Ctrl+O)
2. Browse your saved flows
3. Click to load a flow onto the canvas

### Editing Flows

1. Open the flow
2. Make your changes
3. Save with **File → Save** (Ctrl+S)

### Deleting Flows

Use **File → Manage Flows...** to rename or delete saved flows, or **File → Delete Flow** to remove the currently loaded flow.

## Tips for Building Effective Flows

### Start with Linear Flows
Build simple flows first (A → B → C) to understand how agents work together.

### Add Custom Instructions
Take advantage of the ability to add specific instructions to each agent node.

### Use Verify with AI Chat
Always verify your flow before running it on important documents.

### Test with Chat Output First
Use Chat Output Agent at the end of your flow to review results before switching to a file formatter for final export.

### Name Flows Descriptively
Use names like "C. elegans Expression to WBbt CSV" rather than "Flow 1".

## Troubleshooting

### Flow Won't Run

- **Check connections:** Make sure all agents are connected
- **Verify you saved:** The flow must be saved before running
- **Check the Tools tab:** Make sure you're looking in the right place

### No Output Generated

- **Check output agent:** Make sure you have Chat Output or a File Formatter connected
- **Verify connections:** The output agent must be connected to receive data
- **Check upstream artifacts:** The output agent needs a completed structured extraction or validation artifact to project

### Wrong Data Extracted

- **Refine your instructions:** Add more specific custom instructions to agents
- **Review automatic validation:** Check the extractor node's validation attachments and any validation findings in the review workspace
- **Add validation agents:** Include Ontology Term Resolver Agent or another validation agent when you need custom checks beyond the domain-pack defaults

## Common Questions

### Can I run the same flow on multiple documents?

Yes! You can run a flow one document at a time, or use **[Batch Processing](BATCH_PROCESSING.md)** to run a saved flow against multiple documents automatically with real-time progress tracking.

### Are flow results saved?

Generated files are available during your session. Download files you want to keep before ending your session.

### What's the difference between Chat Output and File Formatters?

- **Chat Output:** Shows results in the chat for review and discussion
- **File Formatters:** Generate downloadable files (CSV, TSV, JSON) from completed flow artifacts

Use Chat Output first to review results, then switch to a File Formatter when ready to export.

## Next Steps

- **[Available Agents](AVAILABLE_AGENTS.md)** - Learn more about each agent
- **[Agent Studio](AGENT_STUDIO.md)** - Use AI Chat to discuss your flows
- **[Best Practices](BEST_PRACTICES.md)** - Tips for writing effective instructions
