# Curation Flows Guide

Curation Flows are visual workflows that let you chain multiple AI agents together. You build them once, save them, and reuse them across documents.

> **Note:** Flows currently support **sequential (linear) pipelines only** - each agent connects to the next in a chain. Branching workflows (one agent connecting to multiple outputs) are coming soon.

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

> **Coming Soon:** Batch PDF processing (currently you load one document at a time)

## Accessing the Flow Builder

1. Click **"Agent Studio"** in the navigation bar
2. Select the **"Flows"** tab
3. The Flow Builder canvas appears on the right, with Opus chat on the left

## Flow Builder Interface

**Opus Chat (Left Panel)**
Chat with Claude Opus 4.5 about your flow - ask for help building it, troubleshooting issues, or understanding what each agent does.

**Agent Palette (Left, below chat)**
A list of available agents organized by category. Click or drag agents onto the canvas.

**Canvas (Center/Right)**
The main workspace where you build your flow by adding agents and connecting them.

**Properties Panel (Right)**
When you select a node, this panel shows its configuration options where you can add custom instructions.

## Available Agents

### Input
| Agent | Description |
|-------|-------------|
| **Initial Instructions** | Starting point - define the task for your flow |

### PDF Extraction
| Agent | Description |
|-------|-------------|
| **General PDF Agent** | Extracts text, tables, and data from PDF documents |
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
| **Ontology Mapping Agent** | Maps free-text labels to ontology term IDs |

### Output
| Agent | Description |
|-------|-------------|
| **Chat Output Agent** | Displays results in the chat for review |
| **CSV File Formatter** | Generates downloadable CSV files |
| **TSV File Formatter** | Generates downloadable TSV files |
| **JSON File Formatter** | Generates downloadable JSON files |

## Building a Flow

### Step 1: Add Agents to the Canvas

**Click to Add:** Find an agent in the Agent Palette and click it to add to the canvas.

**Drag and Drop:** Click and hold on an agent, drag it onto the canvas, and release.

### Step 2: Connect Agents

1. Hover over an agent node to see connection points (handles)
2. Click and drag from one handle to another agent's handle
3. Release to create the connection

**You can connect agents in any direction** - top to bottom, left to right, whatever makes sense for your workflow.

### Step 3: Add Custom Instructions

1. Click on any agent node to select it
2. The Properties Panel shows configuration options
3. Add custom instructions to tell that agent exactly what you want it to do

This is one of the most powerful features - you can fine-tune each step of your workflow.

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

1. In the Flow Builder, click **"Load"**
2. Browse your saved flows
3. Click to load a flow onto the canvas

### Editing Flows

1. Load the flow
2. Make your changes
3. Save again (overwrite or save as new)

### Deleting Flows

1. Click **"Load"** to see saved flows
2. Click the delete icon next to the flow
3. Confirm deletion

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

Yes, but currently you load one document at a time. Load a PDF, run the flow, then load the next PDF and run again. Batch processing is coming soon.

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
