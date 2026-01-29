# Alliance Tools

This directory contains Alliance Genome Resources-specific tool implementations.

## Deployment

These tools are **not loaded directly** from this location. During Alliance deployment, they are copied to `custom/`:

```bash
cp -r backend/tools/alliance_tools/* backend/tools/custom/
```

## For Other Organizations

If you're using this software outside of Alliance:
- You can ignore this directory entirely
- Create your own tools directly in `../custom/`
- Use `../_examples/` as templates

## Tools Included

| Tool | Description |
|------|-------------|
| `agr_curation_query.py` | Query the AGR curation database |
| `agr_disease_lookup.py` | Disease ontology lookups via AGR API |
| `agr_gene_lookup.py` | Gene lookups across all MODs |

## AGR API Integration

These tools connect to the Alliance curation API:
- Base URL configured in `config/connections.yaml`
- Authentication via API key in environment variables
- Handles multi-MOD queries in parallel
- Returns normalized results across organisms

## Notes

- These tools are specifically designed for Alliance infrastructure
- Other organizations will need their own API integrations
- See the tool implementations for patterns you can adapt
