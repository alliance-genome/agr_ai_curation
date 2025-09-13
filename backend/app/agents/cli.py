#!/usr/bin/env python3
"""
CLI interface for PydanticAI agents

Provides command-line access to biocuration agents following constitutional principles.
"""

import asyncio
import json
import sys
from typing import Optional
import click
from pathlib import Path

from .factory import AgentFactory
from .biocuration_agent import BioCurationDependencies
from .models import CurationContext


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """AGR AI Curation Agent CLI - Biological curation with PydanticAI"""
    pass


@cli.command()
@click.argument("message")
@click.option(
    "--model",
    default="openai:gpt-4o",
    help="AI model to use (e.g., openai:gpt-4o, google-gla:gemini-1.5-flash)",
)
@click.option(
    "--format",
    type=click.Choice(["json", "text"]),
    default="text",
    help="Output format",
)
@click.option(
    "--document",
    type=click.Path(exists=True),
    help="Path to document file for context",
)
@click.option(
    "--entities/--no-entities",
    default=True,
    help="Include entity extraction",
)
@click.option(
    "--annotations/--no-annotations",
    default=True,
    help="Include annotation suggestions",
)
def biocurate(
    message: str,
    model: str,
    format: str,
    document: Optional[str],
    entities: bool,
    annotations: bool,
):
    """
    Process a biocuration request.

    Example:
        python -m app.agents.cli biocurate "Extract genes from this text" --model openai:gpt-4o
    """
    try:
        # Load document if provided
        context = None
        if document:
            doc_path = Path(document)
            doc_text = doc_path.read_text()
            context = CurationContext(
                document_text=doc_text,
                document_id=doc_path.name,
            )

        # Create agent
        agent = AgentFactory.get_biocuration_agent(model)

        # Prepare dependencies
        deps = BioCurationDependencies(
            context=context,
            user_preferences={
                "include_entities": entities,
                "include_annotations": annotations,
            },
        )

        # Process request
        result = asyncio.run(agent.process(message, deps))

        # Output result
        if format == "json":
            click.echo(json.dumps(result.model_dump(), indent=2, default=str))
        else:
            click.echo(f"\n{click.style('Response:', fg='green', bold=True)}")
            click.echo(result.response)

            if result.entities and entities:
                click.echo(f"\n{click.style('Entities Found:', fg='blue', bold=True)}")
                for entity in result.entities:
                    click.echo(
                        f"  • {entity.text} ({entity.type.value}) "
                        f"[confidence: {entity.confidence:.2f}]"
                    )

            if result.annotations and annotations:
                click.echo(
                    f"\n{click.style('Suggested Annotations:', fg='yellow', bold=True)}"
                )
                for ann in result.annotations:
                    click.echo(
                        f'  • "{ann.text[:50]}..." - {ann.category} '
                        f"({ann.color.value}) [confidence: {ann.confidence:.2f}]"
                    )

            if result.key_findings:
                click.echo(f"\n{click.style('Key Findings:', fg='magenta', bold=True)}")
                for finding in result.key_findings:
                    click.echo(f"  • {finding}")

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("text")
@click.option(
    "--model",
    default="openai:gpt-4o",
    help="AI model to use",
)
@click.option(
    "--format",
    type=click.Choice(["json", "text"]),
    default="text",
    help="Output format",
)
def extract_entities(text: str, model: str, format: str):
    """
    Extract biological entities from text.

    Example:
        python -m app.agents.cli extract-entities "The p53 gene is a tumor suppressor"
    """
    try:
        agent = AgentFactory.get_entity_extraction_agent(model)
        result = asyncio.run(agent.run(text))

        if format == "json":
            click.echo(json.dumps(result.output.model_dump(), indent=2))
        else:
            click.echo(f"\n{click.style('Extracted Entities:', fg='green', bold=True)}")
            click.echo(result.output.summary)
            click.echo(f"\nTotal entities: {result.output.total_entities}")

            for entity_type, count in result.output.entity_breakdown.items():
                if count > 0:
                    click.echo(f"  {entity_type}: {count}")

            click.echo(f"\n{click.style('Details:', fg='blue', bold=True)}")
            for entity in result.output.entities:
                click.echo(
                    f"  • {entity.text} ({entity.type.value}) "
                    f"[{entity.confidence:.2f}]"
                )
                if entity.normalized_form:
                    click.echo(f"    Normalized: {entity.normalized_form}")
                if entity.database_id:
                    click.echo(f"    ID: {entity.database_id}")

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
def list_models():
    """List available AI models."""
    try:
        models = AgentFactory.get_available_models()

        click.echo(f"\n{click.style('Available Models:', fg='green', bold=True)}")
        for provider, model_list in models.items():
            click.echo(f"\n{click.style(provider.upper(), fg='blue')}:")
            for model in model_list:
                click.echo(f"  • {model}")

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("model", default="openai:gpt-4o")
def test_model(model: str):
    """Test if a model is working correctly."""
    try:
        click.echo(f"Testing model: {model}...")

        success = asyncio.run(AgentFactory.test_model(model))

        if success:
            click.echo(click.style("✓ Model is working correctly", fg="green"))
        else:
            click.echo(click.style("✗ Model test failed", fg="red"))
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("model")
def model_info(model: str):
    """Get information about a specific model."""
    try:
        info = AgentFactory.get_model_info(model)

        if not info:
            click.echo(f"Model {model} not found", err=True)
            sys.exit(1)

        click.echo(f"\n{click.style('Model Information:', fg='green', bold=True)}")
        click.echo(f"Name: {info.get('name')}")
        click.echo(f"Provider: {info.get('provider')}")
        click.echo(f"Description: {info.get('description')}")
        click.echo(f"Supports Streaming: {info.get('supports_streaming')}")
        click.echo(f"Supports Tools: {info.get('supports_tools')}")
        click.echo(f"Max Tokens: {info.get('max_tokens'):,}")

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
def clear_cache():
    """Clear all cached agents."""
    try:
        AgentFactory.clear_cache()
        click.echo(click.style("✓ Agent cache cleared", fg="green"))

    except Exception as e:
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
