# Alliance Agents

This directory contains Alliance Genome Resources-specific agent definitions.

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

| Agent | Description |
|-------|-------------|
| `gene_agent/` | Gene lookups and validation |
| `allele_agent/` | Allele/variant curation |
| `disease_agent/` | Disease ontology lookups |
| `chemical_agent/` | Chemical/compound lookups |
| `gene_ontology_agent/` | GO term searches |
| `go_annotations_agent/` | Gene-to-GO mappings |
| `gene_expression_agent/` | Expression data extraction |
| `orthologs_agent/` | Ortholog relationship queries |
| `ontology_mapping_agent/` | Term-to-ID mappings |
| `pdf_agent/` | PDF document analysis |

## Group Rules

Each agent may contain `group_rules/` with organization-specific rules. For Alliance, these correspond to Model Organism Databases (MODs):
- `fb.yaml` - FlyBase rules
- `wb.yaml` - WormBase rules
- `mgi.yaml` - MGI rules
- etc.

## Migration Status

**Note:** These agent folders currently contain the directory structure only. Agent content (agent.yaml, prompt.yaml, schema.py, group_rules/*.yaml) will be populated during the migration from the existing codebase.
