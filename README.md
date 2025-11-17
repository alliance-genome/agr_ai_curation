# AGR AI Curation System - Curator Guide

Welcome to the Alliance of Genome Resources (AGR) AI Curation System! This guide will help you understand what types of questions the AI can answer and which specialized agents are available to assist with your curation tasks.

## Start Here

**New to the AI Curation System?**

1. **[Getting Started](GETTING_STARTED.md)** - Learn how to access the system, navigate the interface, and run your first queries
2. **[Best Practices](BEST_PRACTICES.md)** - Master the art of writing effective queries for optimal results

Already familiar with the basics? Continue reading below or jump to [Available Agents](AVAILABLE_AGENTS.md) to see all databases and ontologies.

## What Can the AI Help With?

The AI Curation System provides intelligent assistance for biological curation tasks by connecting to various authoritative data sources. You can ask questions about:

- **Gene Expression Curation** - Extract gene expression patterns from research papers with ontology term mapping
- **Disease Ontology** - Disease classifications, hierarchies, and term relationships
- **Chemical Entities** - Chemical compounds and their properties via ChEBI
- **Gene Information** - Gene details across model organisms
- **Gene Ontology** - GO terms, hierarchies, and biological processes
- **GO Annotations** - Gene annotations with evidence codes
- **Orthology Relationships** - Cross-species gene relationships
- **Ontology Term Mapping** - Map anatomical, developmental, and cellular component labels to official term IDs
- **Research Papers** - Information extracted from uploaded PDF documents

## How It Works

Behind the scenes, a **supervisor agent** analyzes your question and routes it to the appropriate specialist agent(s). Each specialist agent connects to specific databases or APIs to retrieve accurate, up-to-date information.

## Available Agents

For a detailed list of all available agents and their data sources, see **[AVAILABLE_AGENTS.md](AVAILABLE_AGENTS.md)**.

## Getting Started

The AI Curation System is currently in active development. For more technical details about the implementation, see the [private prototype repository](https://github.com/alliance-genome/ai_curation_prototype).

## Questions or Feedback?

**The best way to provide feedback:** Use the **triple-dot menu (⋮)** button on any AI response in the chat interface. This automatically captures your prompts, the AI's responses, and all database traces, giving developers the full context they need to help you.

For general questions or suggestions, please reach out to the development team.
