# Curation Flows Guide

Curation Flows are visual workflows that automate multi-step curation tasks. Instead of asking questions one at a time in the chat, you can build reusable pipelines that chain multiple agents together to process documents and generate structured outputs.

## Why Use Curation Flows?

| Chat Interface | Curation Flows |
|----------------|----------------|
| One question at a time | Chain multiple steps together |
| Manual follow-up required | Automated processing |
| Results in chat messages | Export to CSV, TSV, or JSON files |
| Good for exploration | Good for production curation |
| Single document focus | Batch processing ready |

**Best for:**
- Extracting structured data from research papers
- Processing multiple documents with the same workflow
- Generating downloadable files for database import
- Creating repeatable curation pipelines

## Accessing the Flow Builder

1. Click **"Agent Studio"** in the navigation bar
2. Select the **"Flows"** tab
3. You'll see the visual Flow Builder canvas

## Flow Builder Interface

The Flow Builder has three main areas:

### Canvas (Center)
The main workspace where you build your flow by dragging agents and connecting them.

### Agent Palette (Left)
A list of available agents organized by category. Click or drag agents onto the canvas.

### Properties Panel (Right)
When you select a node, this panel shows its configuration options and settings.

## Available Agents

Flows support 12+ agents organized by function:

### Input Agents
| Agent | Description |
|-------|-------------|
| **Task Input** | Starting point for your flow - defines what to process |

### Extraction Agents
| Agent | Description |
|-------|-------------|
| **PDF Agent** | Extracts content from uploaded research papers |
| **Gene Expression Agent** | Extracts gene expression patterns with anatomical/temporal locations |

### Lookup Agents
| Agent | Description |
|-------|-------------|
| **Gene Curation Agent** | Queries AGR database for gene information |
| **Allele Curation Agent** | Queries AGR database for allele data |
| **Disease Ontology Agent** | Looks up disease terms and classifications |
| **Chemical Ontology Agent** | Queries ChEBI for chemical compound data |
| **Gene Ontology Agent** | Searches GO terms and hierarchies |
| **GO Annotations Agent** | Retrieves gene annotations with evidence codes |
| **Alliance Orthologs Agent** | Finds cross-species orthology relationships |

### Validation Agents
| Agent | Description |
|-------|-------------|
| **Ontology Mapping Agent** | Maps labels to official ontology term IDs |

### Output Agents
| Agent | Description |
|-------|-------------|
| **Chat Output** | Sends results to the chat interface |
| **CSV Formatter** | Generates downloadable CSV files |
| **TSV Formatter** | Generates downloadable TSV files |
| **JSON Formatter** | Generates downloadable JSON files |

## Building a Flow

### Step 1: Add Agents to the Canvas

**Method 1 - Click to Add:**
1. Find the agent you want in the Agent Palette
2. Click on it to add it to the canvas

**Method 2 - Drag and Drop:**
1. Click and hold on an agent in the palette
2. Drag it onto the canvas
3. Release to place it

### Step 2: Connect Agents

1. Hover over an agent node to see connection points (handles)
2. Click and drag from an **output handle** (right side)
3. Connect to an **input handle** (left side) of another agent
4. Release to create the connection

**Connection Rules:**
- Flows move left to right (input → processing → output)
- An agent can connect to multiple downstream agents
- Multiple agents can feed into a single agent
- Connections must flow in one direction (no loops)

### Step 3: Configure Agents

1. Click on any agent node to select it
2. The Properties Panel shows available settings
3. Configure options like:
   - Output format preferences
   - Field mappings
   - Filtering criteria

### Step 4: Save Your Flow

1. Click the **"Save"** button in the toolbar
2. Enter a name for your flow
3. Add an optional description
4. Click **"Save"**

Your flow is now stored and can be loaded later.

## Running a Flow

### Manual Execution

1. Make sure your flow is complete (input → processing → output)
2. Click the **"Run"** button in the toolbar
3. If the flow has a Task Input node, enter your task description
4. Click **"Execute"**
5. Watch the flow execute step by step
6. Results appear based on your output agents

### Verify with Claude

Before running a complex flow, you can verify it with Claude:

1. Click the **"Verify with Claude"** button
2. Claude analyzes your flow structure
3. Identifies potential issues or improvements
4. Suggests optimizations
5. Confirms the flow is ready to run

This is especially useful for new flows or when troubleshooting.

## File Outputs

One of the most powerful features of flows is generating downloadable files.

### CSV Formatter

Creates comma-separated value files for spreadsheet applications.

**Use cases:**
- Import into Excel or Google Sheets
- Database bulk import
- Data sharing with collaborators

**Example output:**
```csv
gene_symbol,anatomy_term,anatomy_id,stage,evidence
dmd-3,pharynx,WBbt:0003681,L3,IDA
unc-54,body wall muscle,WBbt:0005781,adult,IEP
```

### TSV Formatter

Creates tab-separated value files, preferred by many databases and bioinformatics tools.

**Use cases:**
- Database import
- AGR data submission
- Sharing with bioinformatics pipelines

**Example output:**
```tsv
gene_symbol	anatomy_term	anatomy_id	stage	evidence
dmd-3	pharynx	WBbt:0003681	L3	IDA
unc-54	body wall muscle	WBbt:0005781	adult	IEP
```

### JSON Formatter

Creates structured JSON files that preserve complex nested data.

**Use cases:**
- Data with hierarchical structure (e.g., expression data with nested anatomy/stage info)
- Sharing with computational biologists
- When you need to preserve relationships between data fields

**Example output:**
```json
{
  "extractions": [
    {
      "gene_symbol": "dmd-3",
      "expression": {
        "anatomy": {"term": "pharynx", "id": "WBbt:0003681"},
        "stage": "L3"
      },
      "evidence": "IDA"
    }
  ]
}
```

### Downloading Files

When a flow completes with a file output:

1. A download card appears in the chat
2. The card shows:
   - File name and format
   - File size
   - Generation timestamp
   - Download count
3. Click the download button to save the file
4. Files are available until the session ends

## Example Workflows

### Example 1: Gene Expression Extraction to CSV

**Goal:** Extract gene expression data from a paper and export to CSV

```
Task Input → PDF Agent → Gene Expression Agent → CSV Formatter
```

**Steps:**
1. Add **Task Input** node
2. Connect to **PDF Agent** (extracts document content)
3. Connect to **Gene Expression Agent** (identifies expression patterns)
4. Connect to **CSV Formatter** (generates downloadable file)
5. Run with task: "Extract all gene expression data from the uploaded paper"

### Example 2: Ontology Mapping Pipeline

**Goal:** Extract anatomical terms and map them to official IDs

```
Task Input → PDF Agent → Gene Expression Agent → Ontology Mapping Agent → TSV Formatter
```

**Steps:**
1. Add **Task Input** node
2. Connect to **PDF Agent**
3. Connect to **Gene Expression Agent**
4. Connect to **Ontology Mapping Agent** (resolves term IDs)
5. Connect to **TSV Formatter**
6. Run with task: "Extract expression data and map all anatomy terms to WBbt IDs"

### Example 3: Multi-Output Flow

**Goal:** Generate both a chat summary and a downloadable file

```
                              → Chat Output (for review)
Task Input → PDF Agent → Gene Expression Agent
                              → CSV Formatter (for download)
```

**Steps:**
1. Build the extraction pipeline
2. Connect Gene Expression Agent to **both** Chat Output and CSV Formatter
3. Run to get results in chat AND as a downloadable file

## Managing Flows

### Saving Flows

- Click **"Save"** to save the current flow
- Give flows descriptive names like "WormBase Expression Extraction"
- Add descriptions explaining what the flow does

### Loading Flows

1. Click **"Load"** in the toolbar
2. Browse your saved flows
3. Click to load a flow onto the canvas
4. Previous canvas content is replaced

### Sharing Flows

Flows are currently saved per-user. To share a flow:
1. Save your flow
2. Export the flow configuration (if available)
3. Share with colleagues who can import it

### Deleting Flows

1. Click **"Load"** to see saved flows
2. Click the delete icon next to the flow you want to remove
3. Confirm deletion

## Tips for Building Effective Flows

### Start Simple
Begin with a linear flow (A → B → C) before building complex branching workflows.

### Test Incrementally
Add one agent at a time and verify it works before adding more.

### Use Verify with Claude
Let Claude check your flow before running it on important documents.

### Choose the Right Output
- **Chat Output** - For quick review and iteration
- **CSV** - For spreadsheets (Excel, Google Sheets)
- **TSV** - For database import and AGR submission
- **JSON** - For complex nested data that needs to preserve structure

### Name Flows Clearly
Use descriptive names like "C. elegans Expression to WBbt CSV" rather than "Flow 1".

### Consider Data Volume
Large documents may produce significant output. Start with smaller test documents.

## Troubleshooting

### Flow Won't Run

- **Check connections:** Ensure all agents are connected in a valid path
- **Verify input:** Task Input node needs a task description
- **Look for disconnected nodes:** All agents should be part of the flow

### No Output Generated

- **Check output agent:** Ensure you have Chat Output or a Formatter agent
- **Verify connections to output:** The output agent must receive data from upstream agents
- **Check task description:** Make sure the task is clear and specific

### Wrong Data Extracted

- **Refine task description:** Be more specific about what you want
- **Add validation:** Include Ontology Mapping Agent for term verification
- **Check agent order:** Ensure agents are connected in logical sequence

### File Format Issues

- **CSV problems:** Check for special characters in data that may need escaping
- **TSV problems:** Verify no tabs in data fields
- **JSON problems:** Validate JSON structure if importing elsewhere

## Common Questions

### Can I run the same flow on multiple documents?

Currently, flows run on documents selected in the current session. For batch processing, you would run the flow once per document.

### Are flow results saved?

Generated files are available during your session. Download files you want to keep before ending your session.

### Can I edit a saved flow?

Yes. Load the flow, make changes, and save again (either overwrite or save as new).

### How do I know which agents to use?

- See **[Available Agents](AVAILABLE_AGENTS.md)** for detailed descriptions of each agent
- Use **Verify with Claude** to get suggestions
- Start with example workflows and modify them

### What's the difference between Chat Output and File Formatters?

- **Chat Output:** Shows results in the chat interface for review and discussion
- **File Formatters:** Generate downloadable files (CSV, TSV, JSON) for external use

Both can be used in the same flow to get results in multiple formats.

## Next Steps

- **[Available Agents](AVAILABLE_AGENTS.md)** - Learn about all agents you can use in flows
- **[Agent Studio](AGENT_STUDIO.md)** - Explore prompts and debug AI behavior
- **[Best Practices](BEST_PRACTICES.md)** - Tips for writing effective task descriptions
