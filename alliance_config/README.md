# Alliance Configuration

This directory contains Alliance Genome Resources-specific configuration files.

## Deployment

These files are **not used directly** from this location. During Alliance deployment, they are copied to `config/`:

```bash
cp alliance_config/groups.yaml config/groups.yaml
cp alliance_config/connections.yaml config/connections.yaml
```

## For Other Organizations

If you're using this software outside of Alliance:
- You can ignore this directory entirely
- Create your own `config/groups.yaml` and `config/connections.yaml`
- Use `config/*.yaml.example` as templates

## Files

| File | Description |
|------|-------------|
| `groups.yaml` | Alliance group definitions (MODs) and Cognito group mappings |
| `connections.yaml` | Alliance external service connections (AGR API, etc.) |

## Groups Configuration

The `groups.yaml` file maps Cognito groups to internal group IDs:
- FlyBase curators → FB
- WormBase curators → WB
- MGI curators → MGI
- etc.

## Connections Configuration

The `connections.yaml` file defines external services:
- AGR Curation API
- Weaviate (vector database)
- LLM providers (OpenAI/Gemini)
