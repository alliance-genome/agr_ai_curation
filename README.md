# AGR AI Curation System - Curator Guide

Welcome to the Alliance of Genome Resources (AGR) AI Curation System! This guide will help you understand the system's capabilities, from asking questions about biological data to building automated curation workflows.

## Start Here

**New to the AI Curation System?**

1. **[Getting Started](GETTING_STARTED.md)** - Learn how to access the system, navigate the interface, and run your first queries
2. **[Best Practices](BEST_PRACTICES.md)** - Master the art of writing effective queries for optimal results
3. **[Available Agents](AVAILABLE_AGENTS.md)** - See all databases, ontologies, and specialist agents

**Ready for advanced features?**

4. **[Agent Studio](AGENT_STUDIO.md)** - Browse prompts, build flows, and chat with Claude Opus 4.5
5. **[Curation Flows](CURATION_FLOWS.md)** - Build visual workflows that chain multiple agents together

## What Can the AI Help With?

The AI Curation System provides intelligent assistance for biological curation tasks by connecting to various authoritative data sources. You can:

### Ask Questions About Biological Data

- **Gene Expression Curation** - Extract gene expression patterns from research papers with ontology term mapping
- **Disease Ontology** - Disease classifications, hierarchies, and term relationships
- **Chemical Entities** - Chemical compounds and their properties via ChEBI
- **Gene Information** - Gene details across model organisms
- **Gene Ontology** - GO terms, hierarchies, and biological processes
- **GO Annotations** - Gene annotations with evidence codes
- **Orthology Relationships** - Cross-species gene relationships
- **Ontology Term Mapping** - Map anatomical, developmental, and cellular component labels to official term IDs
- **Research Papers** - Information extracted from uploaded PDF documents

### Build Automated Curation Workflows

- **Visual Flow Builder** - Create multi-agent workflows using drag-and-drop
- **Chain Specialists Together** - Connect extraction, validation, and output agents
- **Export Results** - Generate CSV, TSV, or JSON files from your workflows
- **Save and Reuse** - Store flows for repeated use across documents

### Understand and Improve AI Behavior

- **Agent Studio** - Browse prompts, build flows, and chat with Claude Opus 4.5
- **Prompt Browser** - View exact instructions given to each agent
- **Discuss Responses** - Use triple-dot menu to discuss any AI response with Opus
- **Submit Suggestions** - Help improve the system with your domain expertise

## How It Works

Behind the scenes, a **supervisor agent** analyzes your question and routes it to the appropriate specialist agent(s). Each specialist agent connects to specific databases or APIs to retrieve accurate, up-to-date information.

### Simple Questions

When you ask a question in the chat, the supervisor routes it to the right specialist:

```
You: "What GO terms are annotated to human TP53?"
     ↓
Supervisor → GO Annotations Agent → Response
```

### Complex Workflows (Curation Flows)

For multi-step tasks, you can build visual flows that chain agents together:

```
Task Input → PDF Agent → Gene Expression Agent → CSV Formatter
                                                      ↓
                                              Downloadable CSV File
```

See **[Curation Flows](CURATION_FLOWS.md)** for complete documentation.

## Key Features

### Chat Interface
The main interaction area for asking questions about uploaded documents or querying databases directly. The supervisor agent automatically routes your questions to the appropriate specialists.

### Agent Studio
Tools for understanding, building, and improving AI behavior:

**Prompts Tab**
- Browse all agent prompts organized by category
- See MOD-specific rules for each agent
- Chat with Opus about how prompts work

**Flows Tab (Curation Flows)**
- Build visual workflows with drag-and-drop
- Chain 12+ agents together (PDF extraction → validation → output)
- Output to chat, CSV, TSV, or JSON files
- Use "Verify with Claude" to check your flow before running
- Save and reuse flows across documents

**Discuss Responses**
- Use triple-dot menu on any chat response to open it in Agent Studio
- Chat with Opus about why the AI gave that specific response

### Audit Panel
Real-time transparency into AI operations:
- Tracks all AI actions and decisions
- Shows which databases were queried
- Displays what information was retrieved
- Provides full traceability

## Available Agents

The system includes 16+ specialist agents organized by function:

| Category | Agents |
|----------|--------|
| **Routing** | Supervisor (routes to specialists) |
| **PDF Extraction** | PDF Agent, Gene Expression Agent |
| **Data Lookup** | Gene, Allele, Disease, Chemical, GO, Orthologs |
| **Validation** | Ontology Mapping, Gene Ontology |
| **Output** | Chat Output, CSV Formatter, TSV Formatter, JSON Formatter |

For detailed agent documentation, see **[Available Agents](AVAILABLE_AGENTS.md)**.

## Flow Output Options

Curation Flows can output results in multiple ways:

| Output | Agent | Use Case |
|--------|-------|----------|
| **Chat** | Chat Output | Quick review and discussion in the chat interface |
| **CSV** | CSV Formatter | Spreadsheet-compatible data (Excel, Google Sheets) |
| **TSV** | TSV Formatter | Tab-separated for database import |
| **JSON** | JSON Formatter | Structured data with nested information |

You can use multiple outputs in the same flow - for example, send results to chat for review AND generate a CSV file for download.

File outputs appear in the chat as downloadable cards with metadata including file size and download count.

## Questions or Feedback?

**The best way to provide feedback:** Use the **triple-dot menu (...)** button on any AI response in the chat interface. This automatically captures your prompts, the AI's responses, and all database traces, giving developers the full context they need to help you.

**Want to understand or improve AI behavior?** Check out **[Agent Studio](AGENT_STUDIO.md)** - browse the exact prompts given to each AI agent, review interaction traces, discuss them with Claude Opus 4.5, and submit improvement suggestions based on your domain expertise.

For general questions or suggestions, please reach out to the development team.

## Documentation Index

| Document | Description |
|----------|-------------|
| [Getting Started](GETTING_STARTED.md) | First-time setup and basic usage |
| [Best Practices](BEST_PRACTICES.md) | Tips for writing effective queries |
| [Available Agents](AVAILABLE_AGENTS.md) | All specialist agents and their capabilities |
| [Agent Studio](AGENT_STUDIO.md) | Browse prompts, build flows, chat with Opus |
| [Curation Flows](CURATION_FLOWS.md) | Visual workflow builder guide |
