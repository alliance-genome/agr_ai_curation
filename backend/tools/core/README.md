# Core Tools

**DO NOT MODIFY** - These tools are managed centrally and ship with the base installation.

## Purpose

Core tools provide generic utilities that any organization can use:
- Vector/hybrid search
- File export (CSV, TSV, JSON)
- Generic REST API client
- SQL query execution

## Planned Tools

These tools will be migrated from the existing codebase:

| Tool | Description |
|------|-------------|
| `weaviate_search.py` | Hybrid vector + keyword search |
| `file_export.py` | Export results to CSV/TSV/JSON |
| `rest_api.py` | Generic REST API client |
| `sql_query.py` | Database query execution |

## For Custom Tools

If you need organization-specific tools:
- Add them to `../custom/` directory
- Do NOT modify files in this directory

## Updates

Core tools are updated through the main repository. Local modifications will be overwritten on updates.
