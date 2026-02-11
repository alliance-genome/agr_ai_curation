# Best Practices for AI Curation

Get the most out of the AI Curation System by following these guidelines for writing effective queries.

## Core Principle: Be Explicit

**Think of the AI as an experienced biocurator who just started working at your MOD (Model Organism Database).**

✅ **What the AI knows:**
- General biocuration concepts and practices
- How to extract data from scientific literature
- How to query databases and ontologies
- Standard data formats and evidence codes

❌ **What the AI may not know:**
- Specific conventions unique to your MOD
- Your organization's preferred ontologies
- Custom annotation rules or policies
- Implicit assumptions about your curation workflow

**The more explicit you are, the better your results will be.**

## 💬 Providing Feedback to Developers

**Found something that didn't work as expected? Have suggestions for improvement?**

Use the **triple-dot menu (⋮)** button on any AI response in the chat interface to submit feedback. This is the **best and easiest way** to help improve the system because it:

- ✅ Automatically captures your exact prompts and the AI's responses
- ✅ Records all database queries and API calls (traces) made during that interaction
- ✅ Stores everything in the database for developer review
- ✅ Provides context developers need to reproduce and fix issues

**We strongly encourage you to use this feature!** Your feedback helps us improve the AI Curation System for everyone.

## Writing Effective Queries

### ❌ Too Vague
"Extract all the genes from this paper and classify them with ontology."

**Problems:**
- Which ontology? (GO? Disease Ontology? Anatomy?)
- What kind of classification? (Function? Location? Process?)
- What format should the output be in?

### ✅ Explicit and Clear
"Extract all C. elegans genes from this paper and classify their anatomical expression patterns using WormBase anatomy ontology terms (WBbt)."

**Why this works:**
- Specifies organism (C. elegans)
- Specifies data type (anatomical expression patterns)
- Specifies exact ontology (WormBase anatomy, WBbt)
- Clear intent (classification task)

## Best Practice Examples

### Gene Expression Curation

❌ "Get gene expression data."

✅ "Extract gene expression data, mapping anatomical locations to WormBase anatomy terms (WBbt) and developmental stages to WormBase life stage terms (WBls)."

### Disease Annotation

❌ "Find diseases mentioned in this paper."

✅ "Identify all human diseases mentioned in this paper and map them to Disease Ontology (DOID) terms with evidence codes."

### GO Annotation

❌ "Annotate this gene with GO terms."

✅ "Create GO annotations for the gene unc-54, focusing on molecular function terms from the Experimental Evidence category (EXP, IDA, IPI, IMP, IGI, IEP)."

### Ontology Mapping

❌ "Map these terms to the right ontology."

✅ "Map the following anatomical terms to WormBase anatomy ontology (WBbt): 'intestine', 'pharynx', 'body wall muscle', 'hypodermis'."

### Gene Information Queries

❌ "Look up this gene."

✅ "Retrieve detailed information for the C. elegans gene daf-16 from WormBase, including function, expression, and phenotype data."

### Chemical Entity Queries

❌ "Find information about this compound."

✅ "Search ChEBI for the chemical entity 'cytidine' and return its molecular formula, synonyms, and classification."

### Orthology Queries

❌ "Get orthologs."

✅ "Find all mammalian orthologs of the C. elegans gene unc-54."

## Know Your Available Agents and Data Sources

The AI Curation System connects to **specialized agents** that query authoritative databases and ontologies. Understanding what's available helps you write better queries.

**📋 See the complete list of agents and their capabilities:** [AVAILABLE_AGENTS.md](AVAILABLE_AGENTS.md)

### Available Agent Categories

- **Ontology Mapping** (45+ ontologies): Anatomical, developmental, phenotype, disease, chemical, and biological ontologies across multiple MODs
- **Gene Information**: WormBase, FlyBase, MGI, RGD, SGD, Xenbase, ZFIN gene data
- **Disease Ontology**: Disease classifications, hierarchies, and relationships (DOID)
- **Gene Ontology**: GO terms, annotations with evidence codes, term hierarchies
- **Chemical Entities**: ChEBI database for compound information
- **Orthology**: Cross-species gene relationships via Alliance API
- **Research Papers**: Uploaded PDF document analysis and extraction

💡 **Pro Tip:** Before asking a question, check [AVAILABLE_AGENTS.md](AVAILABLE_AGENTS.md) to see:
- Which databases are available for your organism
- What ontologies you can map to
- What types of queries each agent supports
- Example queries for each agent type

## Best Practices for Curation Flows

When building **[Curation Flows](CURATION_FLOWS.md)**, follow these guidelines for optimal results:

### Task Descriptions

The task input is the starting point for your flow. Write clear, specific task descriptions:

❌ "Process this paper."

✅ "Extract all C. elegans gene expression data from this paper, mapping anatomical locations to WBbt terms and developmental stages to WBls terms."

### Flow Design

**Start Simple:** Begin with a linear flow (Input → Extract → Output) before building complex pipelines.

**Test Incrementally:** Add one agent at a time and verify results before adding more.

**Use Verify with Claude:** Before running on important documents, let Claude check your flow for issues.

### Choosing Output Formats

| Format | Best For |
|--------|----------|
| **Chat Output** | Quick review, iterative refinement, exploration |
| **CSV** | Spreadsheets, database import, sharing with collaborators |
| **TSV** | Bioinformatics pipelines, AGR data submission |
| **JSON** | API integration, preserving complex nested data |

### Choosing Your Output Agent

A flow terminates when it reaches an output agent, so **use one output agent per flow**. If you want to review results in chat before exporting:

1. First, build your flow with **Chat Output** at the end to review results
2. Once satisfied, swap Chat Output for a **File Formatter** (CSV, TSV, or JSON) to generate the downloadable file

### Flow Naming

Use descriptive flow names that indicate:
- Organism (e.g., "C. elegans", "Zebrafish")
- Data type (e.g., "Expression", "Phenotype")
- Output format (e.g., "to CSV", "to JSON")

**Examples:**
- "WormBase Expression Extraction to TSV"
- "Zebrafish Gene-Disease Mapping to CSV"
- "Multi-species Ortholog Query to JSON"

## Additional Tips

### 1. **Provide Context**
Include relevant details like organism:
- "Extract all C. elegans genes from this paper."
- "Identify all zebrafish genes mentioned in the study."

### 2. **Combine Multiple Steps in One Query**
For complex tasks, you can ask for multiple related actions in a single query:
- "Extract all genes and for each one identify the associated phenotypes and map those to WormBase phenotype ontology terms."

### 3. **Verify and Validate**
Always review the AI's work:
- Check ontology term IDs against source databases
- Verify evidence codes match AGR standards
- Confirm anatomical mappings make biological sense

### 4. **Use the Audit Panel**
The right-side audit panel shows you:
- Which databases were queried
- What API calls were made
- How the AI arrived at its conclusions
- Use this to understand and verify the AI's reasoning

## Common Pitfalls to Avoid

### ❌ Assuming MOD-Specific Knowledge
"Tag this gene with the standard markers we use."
- The AI doesn't know your organization's "standard markers"

### ❌ Using Ambiguous Terms
"Classify with the anatomy ontology."
- Which anatomy ontology? WBbt? FBbt? ZFA? UBERON?

### ❌ Expecting Mind-Reading
"Extract the important genes."
- What makes a gene "important" in this context?

### ❌ Skipping Organism Information
"Find orthologs for this gene."
- Orthologs in which species? All species? Specific MODs?

## Summary

**Remember:** The AI is a powerful assistant with broad knowledge of biocuration practices and access to extensive databases. The key to success is **clear, explicit communication** about:

1. **What** you want (gene data, ontology mapping, annotations, etc.)
2. **Where** to look (specific paper sections, databases, ontologies)
3. **How** to format results (table, JSON, list, etc.)
4. **Which** standards to use (specific ontologies, evidence codes, term IDs)

Treat the AI as a skilled colleague who needs precise instructions to deliver exactly what you need.

## Questions?

If you discover additional best practices or have suggestions for this guide, please share them with the development team!
