# Example Agents (Templates)

This directory contains template agents for reference. These are **not loaded** at runtime (underscore prefix).

## Usage

To create a new agent from these templates:

1. Copy `basic_agent/` to `packages/<package>/agents/your_agent_name/`
2. Rename and customize all files, then declare the agent under
   `agent_bundles` in the owning package's `package.yaml`
3. Restart the application (or trigger config reload)

For an intentional source-development override of a packaged agent, copy only
the changed assets to `config/agents/<agent_id>/`. Deployments provide that
override layer through an explicit `/runtime/config/agents` mount.

## Template Contents

### basic_agent/

A minimal agent template with:
- `agent.yaml` - Commented agent definition
- `prompt.yaml` - Commented prompt template
- `schema.py` - Example Pydantic schema
- `group_rules/` - Example group rule files

## Notes

- Templates use placeholder values - replace all `TODO` and `example` references
- See the design document for complete field documentation
- Each field in YAML files includes comments explaining its purpose
