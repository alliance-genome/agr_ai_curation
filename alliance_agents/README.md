# Alliance Agents

This directory contains Alliance Genome Resources-specific agent definitions.

## Directory Structure

Each agent folder contains:
```
{agent}/
├── agent.yaml       # Agent configuration and metadata
├── prompt.yaml      # Agent's system prompt
├── schema.py        # Pydantic envelope schema (optional)
└── group_rules/     # Organization-specific rules (optional)
    ├── fb.yaml      # FlyBase rules
    ├── wb.yaml      # WormBase rules
    └── ...
```

## Deployment

These agents are **not loaded directly** from this location. During Alliance deployment, they are copied to `config/agents/`:

```bash
cp -r alliance_agents/* config/agents/
```

## For Other Organizations

If you're using this software outside of Alliance:
- You can ignore this directory entirely
- Create your own agents directly in `config/agents/`
- Use `config/agents/_examples/` as templates

## Agents Included

### Validation Agents
| Folder | agent_id | Description |
|--------|----------|-------------|
| `gene/` | gene_validation | Gene lookups and validation |
| `allele/` | allele_validation | Allele/variant curation |
| `disease/` | disease_validation | Disease ontology (DOID) lookups |
| `chemical/` | chemical_validation | Chemical/compound (ChEBI) lookups |
| `gene_ontology/` | gene_ontology_lookup | GO term searches |
| `go_annotations/` | go_annotations_lookup | Gene-to-GO annotation queries |
| `ontology_mapping/` | ontology_mapping_lookup | Free-text to ontology ID mappings |
| `orthologs/` | orthologs_lookup | Ortholog relationship queries |

### Extraction Agents
| Folder | agent_id | Description |
|--------|----------|-------------|
| `pdf/` | pdf_extraction | PDF document analysis |
| `gene_expression/` | gene_expression_extraction | Expression data extraction |

### Output Formatters
| Folder | agent_id | Description |
|--------|----------|-------------|
| `chat_output/` | chat_output_formatter | Display results in chat |
| `csv_formatter/` | csv_output_formatter | Export to CSV format |
| `tsv_formatter/` | tsv_output_formatter | Export to TSV format |
| `json_formatter/` | json_output_formatter | Export to JSON format |

## Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Folder | Short, descriptive | `gene`, `allele` |
| agent_id | function_type | `gene_validation` |
| Schema class | AgentFunctionEnvelope | `GeneValidationEnvelope` |
| Supervisor tool | ask_{folder}_specialist | `ask_gene_specialist` |

## Group Rules

Each agent may contain `group_rules/` with organization-specific rules. For Alliance, these correspond to Model Organism Databases (MODs):
- `fb.yaml` - FlyBase (Drosophila)
- `wb.yaml` - WormBase (C. elegans)
- `mgi.yaml` - MGI (Mouse)
- `rgd.yaml` - RGD (Rat)
- `sgd.yaml` - SGD (Yeast)
- `zfin.yaml` - ZFIN (Zebrafish)
- `hgnc.yaml` - HGNC (Human)

## Migration Status

**Completed:**
- Directory structure with short folder names
- agent.yaml files with descriptive agent_id
- prompt.yaml files exported from database
- schema.py files with envelope classes
- group_rules/*.yaml files for MOD-specific rules
