#!/usr/bin/env python3
"""
Migration script: Export prompts from database to YAML files.

This script exports all active prompts from the prompt_templates table
to the new config-driven architecture format:
- Base prompts -> alliance_agents/{agent}/prompt.yaml
- MOD rules -> alliance_agents/{agent}/group_rules/{mod}.yaml

Usage:
    python scripts/migrate_prompts_to_yaml.py

Environment:
    DATABASE_URL or individual POSTGRES_* variables

Requirements:
    - psycopg2-binary
    - pyyaml
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

import psycopg2
import yaml


# =============================================================================
# Configuration
# =============================================================================

# Database connection (from environment)
# Note: This was a one-time migration script. All values should come from environment.
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5434")
DB_NAME = os.getenv("POSTGRES_DB", "ai_curation")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "alliance_agents"

# Agent name mapping (database name -> folder name)
# Some agents have different names in the database vs what we want for folders
AGENT_NAME_MAP = {
    "pdf": "pdf_agent",
    "gene": "gene_agent",
    "allele": "allele_agent",
    "disease": "disease_agent",
    "chemical": "chemical_agent",
    "supervisor": "supervisor",  # Special case - goes to config/agents/supervisor
    "gene_expression": "gene_expression_agent",
    "gene_ontology": "gene_ontology_agent",
    "go_annotations": "go_annotations_agent",
    "ontology_mapping": "ontology_mapping_agent",
    "orthologs": "orthologs_agent",
    "chat_output": "chat_output_agent",
    "csv_formatter": "csv_formatter_agent",
    "tsv_formatter": "tsv_formatter_agent",
    "json_file_formatter": "json_formatter_agent",
}

# MOD ID to lowercase filename mapping
MOD_FILE_MAP = {
    "FB": "fb",
    "WB": "wb",
    "MGI": "mgi",
    "RGD": "rgd",
    "SGD": "sgd",
    "ZFIN": "zfin",
    "HGNC": "hgnc",
}


# =============================================================================
# Database Functions
# =============================================================================

def get_connection():
    """Create database connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def fetch_active_prompts(conn) -> list:
    """Fetch all active prompts from the database."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                agent_name,
                prompt_type,
                mod_id,
                version,
                content,
                created_at,
                change_notes,
                description
            FROM prompt_templates
            WHERE is_active = true
            ORDER BY agent_name, prompt_type, mod_id
        """)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


# =============================================================================
# YAML Export Functions
# =============================================================================

def create_prompt_yaml(agent_name: str, content: str, version: int,
                       created_at: datetime, change_notes: Optional[str],
                       description: Optional[str]) -> str:
    """Create YAML content for a base prompt."""

    # Build the YAML structure
    yaml_data = {
        "agent_id": agent_name,
        "content": content,
    }

    # Create header comment
    header = f"""# =============================================================================
# AGENT PROMPT: {agent_name}
# =============================================================================
# Exported from database on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Original version: {version}
# Created: {created_at.strftime('%Y-%m-%d') if created_at else 'Unknown'}
"""
    if change_notes:
        header += f"# Notes: {change_notes}\n"
    if description:
        header += f"# Description: {description}\n"
    header += """#
# This file contains the base prompt for the agent. Group-specific rules
# are stored in the group_rules/ subdirectory and injected at runtime.
# =============================================================================

"""

    # Use block scalar style for content, sort_keys=False to preserve field ordering
    yaml_content = yaml.dump(yaml_data, default_flow_style=False, allow_unicode=True, width=1000, sort_keys=False)

    return header + yaml_content


def create_mod_rules_yaml(agent_name: str, mod_id: str, content: str,
                          version: int, created_at: datetime) -> str:
    """Create YAML content for MOD-specific rules."""

    header = f"""# =============================================================================
# GROUP RULES: {mod_id}
# =============================================================================
# Exported from database on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Original version: {version}
# Created: {created_at.strftime('%Y-%m-%d') if created_at else 'Unknown'}
#
# These rules are injected into the {agent_name} agent's prompt when users
# belong to the {mod_id} group.
# =============================================================================

"""

    yaml_data = {
        "group_id": mod_id,
        "content": content,
    }

    # Use sort_keys=False to preserve field ordering (group_id before content)
    yaml_content = yaml.dump(yaml_data, default_flow_style=False, allow_unicode=True, width=1000, sort_keys=False)

    return header + yaml_content


def get_output_path(agent_name: str, prompt_type: str, mod_id: Optional[str]) -> Path:
    """Determine the output path for a prompt."""

    # Map agent name to folder name
    folder_name = AGENT_NAME_MAP.get(agent_name, f"{agent_name}_agent")

    # Special case: supervisor goes to config/agents/supervisor
    if agent_name == "supervisor":
        base_dir = Path(__file__).parent.parent / "config" / "agents" / "supervisor"
    else:
        base_dir = OUTPUT_DIR / folder_name

    if prompt_type == "mod_rules" and mod_id:
        # MOD rules go in group_rules/ subdirectory
        mod_file = MOD_FILE_MAP.get(mod_id, mod_id.lower())
        return base_dir / "group_rules" / f"{mod_file}.yaml"
    else:
        # Base prompts
        return base_dir / "prompt.yaml"


# =============================================================================
# Main Migration Logic
# =============================================================================

def migrate_prompts():
    """Main migration function."""

    print("=" * 70)
    print("Prompt Migration: Database -> YAML")
    print("=" * 70)
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Database: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print()

    # Connect to database
    print("Connecting to database...")
    conn = get_connection()

    # Fetch all active prompts
    print("Fetching active prompts...")
    prompts = fetch_active_prompts(conn)
    print(f"Found {len(prompts)} active prompts")
    print()

    # Track statistics
    stats = {
        "base_prompts": 0,
        "mod_rules": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Process each prompt
    for prompt in prompts:
        agent_name = prompt["agent_name"]
        prompt_type = prompt["prompt_type"]
        mod_id = prompt["mod_id"]
        content = prompt["content"]
        version = prompt["version"]
        created_at = prompt["created_at"]
        change_notes = prompt["change_notes"]
        description = prompt["description"]

        # Skip 'base' type prompts (these seem to be placeholders)
        if prompt_type == "base":
            print(f"  SKIP: {agent_name}:{prompt_type} (placeholder)")
            stats["skipped"] += 1
            continue

        # Determine output path
        output_path = get_output_path(agent_name, prompt_type, mod_id)

        # Create directory if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Generate YAML content
            if prompt_type == "mod_rules" and mod_id:
                yaml_content = create_mod_rules_yaml(
                    agent_name, mod_id, content, version, created_at
                )
                stats["mod_rules"] += 1
            else:
                yaml_content = create_prompt_yaml(
                    agent_name, content, version, created_at, change_notes, description
                )
                stats["base_prompts"] += 1

            # Write file
            output_path.write_text(yaml_content)

            # Log
            rel_path = output_path.relative_to(Path(__file__).parent.parent)
            mod_str = f"/{mod_id}" if mod_id else ""
            print(f"  OK: {agent_name}:{prompt_type}{mod_str} -> {rel_path}")

        except Exception as e:
            print(f"  ERROR: {agent_name}:{prompt_type}/{mod_id}: {e}")
            stats["errors"] += 1

    # Close connection
    conn.close()

    # Print summary
    print()
    print("=" * 70)
    print("Migration Complete")
    print("=" * 70)
    print(f"  Base prompts exported: {stats['base_prompts']}")
    print(f"  MOD rules exported:    {stats['mod_rules']}")
    print(f"  Skipped:               {stats['skipped']}")
    print(f"  Errors:                {stats['errors']}")
    print()

    if stats["errors"] > 0:
        print("WARNING: Some prompts failed to export. Check errors above.")
        return 1

    print("All prompts exported successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(migrate_prompts())
