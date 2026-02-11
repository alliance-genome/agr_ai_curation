# Curation Flows Guide

Curation Flows are visual workflows that let you chain multiple AI agents together. You build them once, save them, and reuse them across documents.

> **Note:** Flows support **sequential (linear) pipelines** - each agent connects to the next in a chain. Each node can have only one outgoing connection.

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

**Repeatable Results**
- Same workflow = consistent extraction across documents
- Great for processing batches of similar papers

> **Tip:** Need to process multiple documents? See **[Batch Processing](BATCH_PROCESSING.md)** to run saved flows against multiple PDFs automatically.

## Accessing the Flow Builder

1. Click **"Agent Studio"** in the navigation bar
2. Select the **"Flows"** tab
3. The Flow Builder canvas appears on the right, with Opus chat on the left

## Flow Builder Interface

**Opus Chat (Left Panel)**
Chat with Claude Opus 4.5 about your flow - ask for help building it, troubleshooting issues, or understanding what each agent does.

**Agent Palette (Left Panel)**
A searchable, collapsible list of available agents organized by category. Click or drag agents onto the canvas. Use the search box to filter agents by name, description, or tools.

**Canvas (Center/Right)**
The main workspace where you build your flow by adding agents and connecting them.

**Properties Panel (Right)**
When you select a node, this panel shows its configuration options where you can add custom instructions.

## Available Agents

### Input
| Agent | Description |
|-------|-------------|
| **Initial Instructions** | Starting point - define the task for your flow |

### Extraction
| Agent | Description |
|-------|-------------|
| **General PDF Agent** | Extracts text, tables, and data from PDF documents |
| **Gene Expression Extractor** | Extracts gene expression patterns from PDFs |

### Validation
| Agent | Description |
|-------|-------------|
| **Gene Validation Agent** | Validates gene identifiers against AGR database |
| **Allele Validation Agent** | Validates allele identifiers against AGR database |
| **Disease Ontology Agent** | Maps disease terms to DOID identifiers |
| **Chemical Ontology Agent** | Maps chemical names to ChEBI identifiers |
| **GO Term Lookup Agent** | Looks up Gene Ontology term definitions |
| **Gene GO Annotations Agent** | Retrieves existing GO annotations for genes |
| **Ortholog Lookup Agent** | Queries orthology relationships across species |
| **Ontology Mapping Agent** | Maps free-text labels to ontology term IDs |

### Output
| Agent | Description |
|-------|-------------|
| **Chat Output Agent** | Displays results in the chat for review |
| **CSV File Formatter** | Generates downloadable CSV files |
| **TSV File Formatter** | Generates downloadable TSV files |
| **JSON File Formatter** | Generates downloadable JSON files |

### My Custom Agents

If you've created custom agents in the **Prompt Workshop**, they appear here under "My Custom Agents". You can use them in flows just like system agents. See **[Agent Studio](AGENT_STUDIO.md)** for details on creating custom agents.

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

Click on any agent node to open the **Properties Panel** on the right. This panel lets you fine-tune how each step in your flow behaves.

**Custom Instructions**

Add specific instructions for this step. These are prepended to the agent's system prompt with highest priority — they override the agent's default behavior for this flow step. Example: "Focus only on gene expression data from the methods section."

**Input Source**

Choose where this step gets its input:

- **Previous Step Output** - Uses the output from the connected upstream step (default when a connection exists)
- **Custom (with variables)** - Write a custom input template using `{{variable}}` syntax to reference outputs from any earlier step

**Variable Templating**

When using a custom input source, reference earlier step outputs by their variable name:

```
Validate these genes: {{pdf_output}}
Cross-reference with: {{expression_data}}
```

Click the variable chips shown below the text field to insert available variables. Variable names must match an earlier step's output variable name exactly.

**Output Variable Name**

Set a name for this step's output so later steps can reference it with `{{variable_name}}`. Names can contain letters, numbers, and underscores. Example: `validated_genes`

**View Base Prompt & MOD Rules**

Click the **"View base prompt & MOD rules"** link to see the full prompt and any MOD-specific overrides for this agent.

### Step 4: Verify with Claude

Before saving, click the **"Verify with Claude"** button. Claude will:
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

**Verify with Claude** - Appears when your flow has nodes. Sends the flow to Claude for structural review before running.

## Flow Validation

The Flow Builder validates your flow and shows error indicators when there are issues:

- **Missing task instructions** - The Initial Instructions node requires non-empty instructions
- **Ambiguous input source** - A validation agent has multiple upstream extractors without an explicit input configuration. Open the Properties Panel and select an input source.
- **Parallel connections** - A node has more than one outgoing connection. Each node can connect to only one downstream step.
- **Duplicate Initial Instructions** - Only one Initial Instructions node is allowed per flow

Validation errors appear as a red banner in the Properties Panel when you select the affected node.

## How Flows Execute

Understanding how flows run helps you build effective workflows:

1. **Initial Instructions** provide the starting task description and context
2. A supervisor agent receives all steps and executes them **sequentially** in the order defined by your connections
3. Each step's output is stored under its **output variable name** and available to later steps via `{{variable}}` references
4. When a step produces a final output (e.g., a file formatter generates a CSV, or Chat Output displays results), the flow **terminates**
5. Custom instructions for each step are applied with highest priority, overriding the agent's default behavior for that step

**Important:** Because the flow terminates when it reaches an output agent, place your output agent at the end of the chain. Only one output agent will produce results per flow run.

## Running a Flow

After building and saving your flow:

1. **Navigate to the main chat screen** (click "Home" in the navigation bar)
2. **Load a PDF document** if your flow uses PDF extraction agents
3. **Click the "Tools" tab** on the right panel
4. **Find your saved flow** in the list
5. **Click the "Run" button** next to your flow

The flow executes and results appear based on your output agent (chat message or downloadable file).

## Output Options

Flows can output results in different ways. Choose the output agent that fits your needs.

### Chat Output Agent

Sends results directly to the chat interface.

**Use cases:**
- Quick review before generating a file
- Iterating on your flow to get the output right
- Discussing results with Opus in Agent Studio

### CSV File Formatter

Creates comma-separated value files for spreadsheet applications.

**Use cases:**
- Import into Excel or Google Sheets
- Database import
- Sharing with collaborators

### TSV File Formatter

Creates tab-separated value files, preferred by many databases.

**Use cases:**
- Database import
- AGR data submission
- Bioinformatics tools

### JSON File Formatter

Creates structured JSON files that preserve complex nested data.

**Use cases:**
- Data with hierarchical structure
- Sharing with computational biologists

### Downloading Files

When a flow generates a file:
1. A download card appears in the chat
2. Click the download button to save the file
3. Files are available until the session ends

## Example Workflows

### Example 1: Gene Expression Extraction to CSV

**Goal:** Extract gene expression data from a paper and export to CSV

```
Initial Instructions → General PDF Agent → Gene Expression Extractor → CSV File Formatter
```

**Instructions for Initial Instructions node:**
"Extract all gene expression data from this paper, including anatomical locations and developmental stages."

### Example 2: Ontology Mapping Pipeline

**Goal:** Extract expression data and map terms to official IDs

```
Initial Instructions → General PDF Agent → Gene Expression Extractor → Ontology Mapping Agent → TSV File Formatter
```

**Instructions for Ontology Mapping Agent node:**
"Map all anatomy terms to WBbt IDs and all stage terms to WBls IDs."

### Example 3: Full Pipeline with File Export

**Goal:** Extract expression data, validate terms, and export to TSV

```
Initial Instructions → General PDF Agent → Gene Expression Extractor → Gene Validation Agent → TSV File Formatter
```

**Instructions for Initial Instructions node:**
"Extract gene expression data and validate all gene identifiers before export."

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

### Use Verify with Claude
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

### Wrong Data Extracted

- **Refine your instructions:** Add more specific custom instructions to agents
- **Add validation agents:** Include Ontology Mapping Agent for term verification

## Common Questions

### Can I run the same flow on multiple documents?

Yes! You can run a flow one document at a time, or use **[Batch Processing](BATCH_PROCESSING.md)** to run a saved flow against multiple documents automatically with real-time progress tracking.

### Are flow results saved?

Generated files are available during your session. Download files you want to keep before ending your session.

### What's the difference between Chat Output and File Formatters?

- **Chat Output:** Shows results in the chat for review and discussion
- **File Formatters:** Generate downloadable files (CSV, TSV, JSON)

Use Chat Output first to review results, then switch to a File Formatter when ready to export.

## Next Steps

- **[Available Agents](AVAILABLE_AGENTS.md)** - Learn more about each agent
- **[Agent Studio](AGENT_STUDIO.md)** - Chat with Opus about your flows
- **[Best Practices](BEST_PRACTICES.md)** - Tips for writing effective instructions
