# Available AI Curation Agents

Use **Agent Studio → Agents** to inspect the agents available to you, including their prompts, tools, output fields, and validation. Installed packages and group access determine what appears. The list below describes the main tasks; an agent's availability does not mean its output supports final Alliance submission.

## Extraction agents

| Agent | Use it for |
|-------|------------|
| **General PDF Extraction Agent** | Custom information from a paper, using a custom output structure or flexible fields |
| **Gene Expression Extractor** | Expression observations in the supported packaged expression format |
| **Gene Extraction Agent** | Gene mentions and paper evidence |
| **Allele/Variant Extraction Agent** | Allele or variant observations and paper evidence |
| **Disease Extraction Agent** | Disease observations in the supported packaged format |
| **Phenotype Extraction Agent** | Phenotype observations in the supported packaged format |

Extractors read the paper and preserve evidence. Supported attached validators resolve biological identifiers or terms afterward. Inspect the output fields before choosing an extractor: a prompt cannot add undeclared fields to a packaged format. Use [Custom data extraction](CUSTOM_OUTPUT_STRUCTURES.md) when your task needs a different set of details.

New extraction drafts use **GPT-6 Astra with low reasoning** by default. Existing saved agents and flow revisions retain their model choices. Studio AI Chat uses Astra with medium reasoning, while validation agents retain their Terra settings.

RGD curators also have group-restricted [GO and disease paper-review recipes](RGD_GO_DISEASE_PAPER_REVIEW.md). Use the recipe guide for those tasks.

## Lookup and validation agents

These agents have different roles. Some retrieve existing annotations or relationships; others provide validators for supported extraction fields. Being listed here does not mean an agent can be attached to every custom field. The field editor's validator picker shows compatible built-in and custom choices.

| Agent | Purpose | Example request |
|-------|---------|-----------------|
| **Gene Validation Agent** | Resolve gene symbols, names, identifiers, or cross-references against Alliance curation records | “Resolve daf-16 in C. elegans.” |
| **Allele Validation Agent** | Resolve allele mentions using identifiers, symbols, or descriptions, with species or gene context when known | “Resolve e1370 in C. elegans, associated with daf-16.” |
| **Ontology Term Resolver Agent** | Resolve identifiers, labels, or synonyms within supported ontology types | “Resolve linker cell using WormBase anatomy terms.” |
| **Disease Ontology Agent** | Look up disease terms and relationships | “Show the definition and parent terms for DOID:162.” |
| **Chemical Ontology Agent** | Look up chemicals in ChEBI | “Find cytidine and its ChEBI identifier.” |
| **GO Term Lookup Agent** | Look up GO definitions and relationships through QuickGO | “Show child terms of DNA repair.” |
| **Gene GO Annotations Agent** | Retrieve existing GO annotations and evidence codes | “Show human TP53 annotations with IDA evidence.” |
| **Ortholog Lookup Agent** | Retrieve cross-species orthology relationships | “Find mouse orthologs of human TP53.” |

## Output Formatter Agents

Use these agents in [curation flows](CURATION_FLOWS.md) to present collected results in chat or as downloadable files. Configure the desired columns and formatting in the output step.

| Agent Name | Output Format | Description | Use Cases |
|-----------|---------------|-------------|-----------|
| **Chat Output Agent** | Chat Message | Sends formatted results to the chat interface for review and discussion. | Quick review, iterative refinement, sharing results in conversation |
| **CSV File Formatter** | CSV File | Generates comma-separated value files for spreadsheet applications. | Excel/Google Sheets, database import, data sharing |
| **TSV File Formatter** | TSV File | Generates tab-separated value files preferred by many databases. | Tab-separated tables for downstream tools |
| **JSON File Formatter** | JSON File | Generates structured JSON files preserving complex nested data. | Data with hierarchical structure, sharing with computational biologists |

### File Output Features

When flows generate files, they appear in the chat as downloadable cards showing:

- File name and format
- File size
- Generation timestamp
- Model used for generation
- Download count

Download results you need to keep. A file formatter arranges collected data; it does not make an export a valid Alliance submission.

## Resolve an ontology term

Give the **Ontology Term Resolver Agent** the identifier when you have one. If you have a label, specify the organism and the kind of term you need. For example:

- “Resolve ‘linker cell’ using WormBase anatomy terms.”
- “Resolve ‘L3 larval stage’ using WormBase life stage terms.”
- “Resolve ‘nucleus’ as a GO cellular component.”

Anatomy and life-stage lookups use provider context. Other label or synonym lookups need the ontology type. AI Chat can help identify the supported type; you do not need to memorize the internal type names. GO lookups can also be narrowed to molecular function, biological process, or cellular component. Use the Chemical Ontology Agent for ChEBI.

Supported lookup areas include anatomy, life stage, phenotype, disease, experimental conditions, cell types, sequence and genetics, evidence and quality, pathways, and taxonomy. Inspect the agent's tool documentation for the exact available types. Labels may be shared by several terms, so review unresolved or ambiguous candidates rather than choosing an identifier solely because its name looks familiar.

## Custom agents and field validators

Open **Agent Workshop → Custom data extraction** to define one item type and its details. You can also start from a template, configure an agent from scratch, or clone a saved agent. AI Chat can propose changes to prompts, settings, tools, custom fields, parts, and compatible validator attachments.

For gene or allele identity, use a compatible validator on the relevant detail or part. The allele validator accepts a mention such as an identifier, symbol, or short description; species and associated gene can provide context. It does not require a separate paper-quote field. Ambiguous descriptions may remain unresolved. See [allele validation](CUSTOM_OUTPUT_STRUCTURES.md#validate-an-allele).

The validator picker includes eligible custom validators as well as built-in choices. A custom validator must retain a supported validator contract; an arbitrary agent named “validator” is not sufficient. No stock, supplier, or catalog-number validator is currently provided.

Save the custom agent before using it in a flow. Existing flows and validator attachments keep their saved revisions when you later edit an agent. See [Agent Studio](AGENT_STUDIO.md) for sharing, versions, and draft recovery.

## Suggestions for New Agents

If a needed capability is missing, use the tool request option in Workshop or send feedback with an example task and the data source you need.
