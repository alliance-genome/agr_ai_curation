# Adding a New Agent

Step-by-step guide to adding a new agent to the AI Curation system using the config-driven architecture.

> **Time**: 15-30 minutes
> **Prerequisites**: Docker running, backend accessible

---

## Overview

Agents are self-contained folders with YAML configuration and Python schema. No code changes required in the core system.

```
my_agent/
├── agent.yaml        # Agent definition
├── prompt.yaml       # Instructions
├── schema.py         # Output format
└── group_rules/      # Optional: org-specific behavior
```

---

## Step 1: Copy the Template

```bash
cd config/agents
cp -r _examples/basic_agent my_agent
```

---

## Step 2: Define Your Agent (agent.yaml)

Edit `my_agent/agent.yaml`:

```yaml
# Must match folder name
agent_id: my_agent

# Display name in UI
name: "My Agent"

# Brief description
description: "Validates and processes [domain] data"

# CRITICAL: Tells supervisor when to route to this agent
# Be specific - vague descriptions cause routing errors
supervisor_routing:
  description: "Use for validating [specific data type], querying [specific domain], or looking up [specific information]"

# Tools this agent can use (must exist in backend/tools/)
tools:
  - agr_curation_query    # Primary database access
  # - alliance_api_call   # REST API calls
  # - weaviate_search     # Document search

# Class name from schema.py
output_schema: MyAgentEnvelope

# LLM settings (supports environment variables)
model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-4o}"
  temperature: 0.1
  reasoning: "medium"

# Set true if different orgs need different behavior
group_rules_enabled: false
```

### Supervisor Routing Tips

| Good Description | Bad Description |
|-----------------|-----------------|
| "Use for validating gene symbols, looking up gene IDs, or checking gene existence" | "Use for gene stuff" |
| "Use for extracting chemical compounds from documents" | "Handles chemicals" |
| "Use for mapping free-text to ontology terms" | "Does ontology" |

---

## Step 3: Write the Prompt (prompt.yaml)

Edit `my_agent/prompt.yaml`:

```yaml
agent_id: my_agent

content: |
  You are a [Domain] Specialist for the AI Curation system.

  ## Your Role

  [Clear description of what this agent does and why it exists]

  ## Capabilities

  You can help users with:
  - [Capability 1]
  - [Capability 2]
  - [Capability 3]

  ## Tools Available

  - **agr_curation_query**: Query the Alliance database for genes, alleles, etc.
    - Returns structured results with identifiers and metadata
    - Supports filtering by species, symbol, ID

  ## Instructions

  When handling a request:
  1. Parse the user's query to identify what they're looking for
  2. Call the appropriate tool(s) to gather data
  3. Validate and format the results
  4. Return a structured response using the schema

  ## Output Format

  Always return results using the MyAgentEnvelope schema:
  - `results`: List of validated items
  - `not_found`: Items that couldn't be validated
  - `warnings`: Any issues encountered

  ## Constraints

  - Only return items that exist in the database
  - Never fabricate or guess identifiers
  - Clearly distinguish between "not found" and "error"
```

### Prompt Writing Tips

- Be specific about what the agent can and cannot do
- Describe each tool and what it returns
- Give clear instructions for the workflow
- Define the output format explicitly
- Include constraints to prevent hallucination

---

## Step 4: Define the Output Schema (schema.py)

Edit `my_agent/schema.py`:

```python
"""Output schema for My Agent."""

from pydantic import BaseModel, Field
from typing import List, Optional


class MyResult(BaseModel):
    """A single result item."""

    # Required fields
    id: str = Field(description="Unique identifier (CURIE format)")
    name: str = Field(description="Human-readable name")
    valid: bool = Field(description="Whether the item was found/validated")

    # Optional fields
    species: Optional[str] = Field(
        default=None,
        description="Species (if applicable)"
    )
    source: Optional[str] = Field(
        default=None,
        description="Data source that provided this result"
    )


class MyAgentEnvelope(BaseModel):
    """
    Container for results.

    This is the class referenced in agent.yaml's output_schema field.
    All list fields MUST have default_factory to handle empty results.
    """

    # Results found
    results: List[MyResult] = Field(
        default_factory=list,
        description="Successfully validated items"
    )

    # Items not found
    not_found: List[str] = Field(
        default_factory=list,
        description="Identifiers that could not be found"
    )

    # Warnings/issues
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered"
    )

    # Optional query info
    query_summary: Optional[str] = Field(
        default=None,
        description="Summary of what was searched"
    )
```

### Schema Rules

| Do | Don't |
|-----|-------|
| Use `Field(default_factory=list)` for lists | Use `Field(...)` (required) for envelope lists |
| Add `description` to every field | Leave fields undocumented |
| Use `Optional[X] = Field(default=None)` | Make optional fields required |
| Keep schemas flat | Create deeply nested structures |

---

## Step 5: Add Group Rules (Optional)

If different organizations need different behavior, create `my_agent/group_rules/`:

```bash
mkdir -p my_agent/group_rules
```

Create `my_agent/group_rules/fb.yaml` for FlyBase:

```yaml
group_id: FB

rules: |
  ## FlyBase-Specific Rules

  When handling FlyBase data:
  - Use the FB: prefix for FlyBase identifiers
  - Check for CG numbers as alternative identifiers
  - FlyBase genes may have multiple valid symbols (synonyms)
```

Create similar files for other groups (`wb.yaml`, `mgi.yaml`, etc.).

**Important**: The filename (without `.yaml`) must match the `group_id` in `config/groups.yaml`.

---

## Step 6: Verify and Test

### Check YAML Syntax

```bash
# Quick syntax check
python3 -c "import yaml; yaml.safe_load(open('config/agents/my_agent/agent.yaml'))"
python3 -c "import yaml; yaml.safe_load(open('config/agents/my_agent/prompt.yaml'))"
```

### Check Schema Imports

```bash
# Schemas are dynamically discovered - just verify the Python file is valid
python3 -m py_compile config/agents/my_agent/schema.py && echo "Schema syntax OK"
```

### Restart Backend

```bash
docker compose restart backend
```

### Test in Chat

Open the Agent Studio and ask a question that should route to your agent:

> "Can you validate these [domain items]: item1, item2, item3"

Check the logs:

```bash
docker compose logs backend | grep my_agent
```

---

## Step 7: Refine

Based on testing:

1. **Routing issues**: Improve `supervisor_routing.description`
2. **Wrong output format**: Check schema matches what LLM returns
3. **Missing tools**: Add tools to the `tools` list
4. **Group-specific issues**: Add/update group rules

No restart needed for prompt changes - just restart backend.

---

## Complete Example: Disease Validation Agent

```yaml
# agent.yaml
agent_id: disease_validation
name: "Disease Validation Agent"
description: "Validates disease terms and maps to DO ontology"

supervisor_routing:
  description: "Use for validating disease names, looking up DOID identifiers, or mapping text to Disease Ontology terms"

tools:
  - agr_curation_query
  - curation_db_sql

output_schema: DiseaseValidationEnvelope

model_config:
  model: "${AGENT_DISEASE_MODEL:-gpt-4o}"
  temperature: 0.0
  reasoning: "low"

group_rules_enabled: false
```

```yaml
# prompt.yaml
agent_id: disease_validation

content: |
  You are a Disease Validation Specialist for the Alliance of Genome Resources.

  ## Your Role

  Validate disease terms against the Disease Ontology (DO) and return structured results with DOID identifiers.

  ## Tools

  - **agr_curation_query**: Search the Alliance database for disease terms
  - **curation_db_sql**: Direct SQL queries for complex lookups

  ## Instructions

  1. Parse disease names from the user's query
  2. Search for exact and fuzzy matches
  3. Return validated terms with DOIDs
  4. List any terms that couldn't be matched
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Agent not appearing | Check folder name doesn't start with `_` |
| "Schema not found" error | Verify `output_schema` matches class name exactly |
| Supervisor not routing | Improve `supervisor_routing.description` |
| Empty results | Check schema uses `default_factory=list` |
| Tool errors | Verify tool exists and is exported |

---

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](./CONFIG_DRIVEN_ARCHITECTURE.md) - Full architecture guide
- [ADDING_NEW_TOOL.md](./ADDING_NEW_TOOL.md) - Adding custom tools
- [config/agents/README.md](../../../config/agents/README.md) - Agent directory reference
- [config/agents/_examples/](../../../config/agents/_examples/) - Template files
