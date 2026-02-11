# Getting Started with AI Curation

Welcome! This guide will walk you through your first steps using the AI Curation System.

## Accessing the System

1. Navigate to **[ai-curation.alliancegenome.org](https://ai-curation.alliancegenome.org)**
2. Log in with your **AWS Cognito credentials**

## Understanding the Interface

The AI Curation System has three main panels:

### Left Panel: PDF Viewer
- Displays uploaded research papers
- Allows you to view and navigate through documents
- Shows highlighted sections relevant to your queries

### Middle Panel: Chat Interface
- Your main interaction area with the AI assistant
- Ask questions about uploaded documents or query databases directly
- Receive detailed responses with citations and evidence
- **Provide feedback** using the triple-dot menu (⋮) on any AI response - this automatically captures your prompts, responses, and all traces for developer review

### Right Panel: Audit Trail & Tools
The right panel has two tabs:

**Audit Tab**
- Tracks all AI actions and decisions
- Shows which databases were queried
- Displays API calls and data sources used
- Provides transparency into how answers were generated

**Tools Tab**
- Lists your saved curation flows
- Click **"Run"** next to any flow to execute it against the current document
- Quick access to run workflows without switching to Agent Studio

## Uploading Documents

To work with research papers:

1. Click **"Documents"** in the top navigation bar
2. Click **"Upload Documents"**
3. Select your PDF file(s)
4. Wait for the upload to complete
5. Once uploaded, you can start chatting about the document

## Querying Databases

You don't need to upload documents to use the AI Curation System! You can:

- Ask questions about genes, diseases, ontologies, and more
- Query authoritative databases directly
- Get cross-referenced information from multiple sources

The system will automatically determine which databases to query based on your question.

## Example Queries

### With Uploaded Documents
- "Extract all C. elegans gene expression data from this paper and map anatomical locations to WormBase anatomy terms (WBbt)."
- "Identify anatomical structures mentioned in the methods section and map them to Zebrafish Anatomy Ontology (ZFA) terms."
- "Map the developmental stages in Table 2 to WormBase life stage terms (WBls)."

### Database Queries (No Document Upload Needed)
- "What is the function of the gene daf-16 in C. elegans?"
- "Show me the GO annotations for human TP53 with experimental evidence codes."
- "What are the child terms of DOID:162 (cancer) in the Disease Ontology?"

## Next Steps

### Learn the Basics
- Review **[Best Practices](BEST_PRACTICES.md)** for tips on writing effective queries
- Check **[Available Agents](AVAILABLE_AGENTS.md)** to see all available databases and ontologies
- Start asking questions and exploring!

### Explore Advanced Features
- **[Agent Studio](AGENT_STUDIO.md)** - Chat with Claude Opus 4.5 about prompts, browse agent configurations, and analyze traces
- **[Curation Flows](CURATION_FLOWS.md)** - Build visual workflows that chain agents together and export results to CSV, TSV, or JSON files
- **[Batch Processing](BATCH_PROCESSING.md)** - Run saved flows against multiple documents automatically with real-time progress tracking
- **Prompt Workshop** (in Agent Studio) - Create custom versions of agent prompts with per-MOD overrides and use them in your curation flows

## Need Help?

If you encounter issues or have questions:

1. **Use the feedback button** - Click the triple-dot menu (⋮) on any AI response to submit feedback directly through the system. This is the easiest way to report issues because it automatically captures all the context developers need.

2. **Contact the development team** - For general questions or suggestions not related to a specific interaction.
