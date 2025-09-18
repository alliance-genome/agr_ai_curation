#!/usr/bin/env python3
"""Analyze LangSmith traces for the AI Curation system."""

import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
import json
from collections import defaultdict

from langsmith import Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def analyze_traces(project_name: str = "ai-curation-dev", limit: int = 10):
    """Analyze recent traces from LangSmith."""

    # Initialize client
    client = Client(api_key=os.getenv("LANGSMITH_API_KEY"))

    print(f"🔍 Analyzing LangSmith traces for project: {project_name}")
    print("=" * 70)

    try:
        # Get recent runs
        runs = list(client.list_runs(
            project_name=project_name,
            limit=limit,
            is_root=True  # Only get root runs
        ))

        if not runs:
            print("No runs found in the project.")
            return

        print(f"\n📊 Found {len(runs)} recent runs\n")

        # Analyze each run
        for i, run in enumerate(runs, 1):
            print(f"\n{'='*70}")
            print(f"Run #{i}: {run.name}")
            print(f"ID: {run.id}")
            print(f"Status: {run.status}")
            print(f"Start: {run.start_time}")

            # Calculate latency
            if run.end_time and run.start_time:
                latency = (run.end_time - run.start_time).total_seconds()
                print(f"Total Latency: {latency:.2f}s")

            # Get metadata
            if run.extra and 'metadata' in run.extra:
                print(f"\n📝 Metadata:")
                metadata = run.extra['metadata']
                for key, value in metadata.items():
                    if isinstance(value, (list, dict)):
                        print(f"  {key}: {json.dumps(value, indent=2)}")
                    else:
                        print(f"  {key}: {value}")

            # Get child runs to see the flow
            child_runs = list(client.list_runs(
                project_name=project_name,
                filter=f'eq(parent_run_id, "{run.id}")',
                limit=50
            ))

            if child_runs:
                print(f"\n🔄 Execution Flow ({len(child_runs)} steps):")

                # Group by node type
                node_types = defaultdict(list)
                for child in child_runs:
                    if child.extra and 'metadata' in child.extra:
                        node_type = child.extra['metadata'].get('node_type', 'unknown')
                        node_types[node_type].append(child)

                # Show execution order
                sorted_children = sorted(child_runs, key=lambda x: x.start_time or datetime.min)
                for j, child in enumerate(sorted_children[:20], 1):  # Show first 20
                    if child.end_time and child.start_time:
                        child_latency = (child.end_time - child.start_time).total_seconds() * 1000
                        latency_str = f"{child_latency:.0f}ms"
                    else:
                        latency_str = "N/A"

                    # Get node metadata
                    node_info = ""
                    if child.extra and 'metadata' in child.extra:
                        meta = child.extra['metadata']
                        if 'node_type' in meta:
                            node_info = f" [{meta['node_type']}]"
                        if 'description' in meta:
                            node_info += f" - {meta['description']}"

                    print(f"  {j:2}. {child.name}{node_info} ({latency_str})")

                if len(sorted_children) > 20:
                    print(f"  ... and {len(sorted_children) - 20} more steps")

                # Summary by node type
                print(f"\n📈 Summary by Node Type:")
                for node_type, runs in node_types.items():
                    avg_latency = sum(
                        (r.end_time - r.start_time).total_seconds() * 1000
                        for r in runs
                        if r.end_time and r.start_time
                    ) / len(runs) if runs else 0
                    print(f"  - {node_type}: {len(runs)} calls, avg {avg_latency:.0f}ms")

            # Get any errors
            if run.error:
                print(f"\n❌ Error: {run.error}")

            # Show inputs/outputs sample
            if run.inputs:
                print(f"\n📥 Inputs:")
                input_str = json.dumps(run.inputs, indent=2)
                if len(input_str) > 500:
                    print(f"{input_str[:500]}...")
                else:
                    print(input_str)

            if run.outputs:
                print(f"\n📤 Outputs:")
                output_str = json.dumps(run.outputs, indent=2) if isinstance(run.outputs, dict) else str(run.outputs)
                if len(output_str) > 500:
                    print(f"{output_str[:500]}...")
                else:
                    print(output_str)

        # Overall statistics
        print(f"\n{'='*70}")
        print("📊 OVERALL STATISTICS")
        print(f"{'='*70}")

        successful_runs = [r for r in runs if r.status == "success"]
        failed_runs = [r for r in runs if r.status == "error"]

        print(f"Success Rate: {len(successful_runs)}/{len(runs)} ({len(successful_runs)/len(runs)*100:.1f}%)")

        if successful_runs:
            latencies = [
                (r.end_time - r.start_time).total_seconds()
                for r in successful_runs
                if r.end_time and r.start_time
            ]
            if latencies:
                print(f"Average Latency: {sum(latencies)/len(latencies):.2f}s")
                print(f"Min Latency: {min(latencies):.2f}s")
                print(f"Max Latency: {max(latencies):.2f}s")

        if failed_runs:
            print(f"\n⚠️ Failed Runs: {len(failed_runs)}")
            for run in failed_runs[:3]:
                print(f"  - {run.name}: {run.error}")

    except Exception as e:
        print(f"❌ Error accessing LangSmith: {e}")
        print("\nMake sure:")
        print("1. LANGSMITH_API_KEY is set correctly")
        print("2. The project name exists")
        print("3. You have access to the project")


def get_detailed_run(run_id: str, project_name: str = "ai-curation-dev"):
    """Get detailed information about a specific run."""

    client = Client(api_key=os.getenv("LANGSMITH_API_KEY"))

    try:
        run = client.read_run(run_id)

        print(f"\n🔎 DETAILED RUN ANALYSIS")
        print("=" * 70)
        print(f"Run: {run.name}")
        print(f"ID: {run.id}")

        # Get full execution tree
        child_runs = list(client.list_runs(
            project_name=project_name,
            filter=f'eq(parent_run_id, "{run.id}")',
            limit=100
        ))

        print(f"\n🌳 Execution Tree:")
        _print_tree(client, project_name, run, level=0)

        # Analyze token usage if available
        if run.extra and 'tokens' in run.extra:
            print(f"\n💰 Token Usage:")
            tokens = run.extra['tokens']
            print(f"  Total: {tokens.get('total', 'N/A')}")
            print(f"  Prompt: {tokens.get('prompt', 'N/A')}")
            print(f"  Completion: {tokens.get('completion', 'N/A')}")

    except Exception as e:
        print(f"❌ Error getting run details: {e}")


def _print_tree(client, project_name, run, level=0, max_level=3):
    """Recursively print execution tree."""
    if level > max_level:
        return

    indent = "  " * level

    # Calculate latency
    latency_str = "N/A"
    if run.end_time and run.start_time:
        latency = (run.end_time - run.start_time).total_seconds() * 1000
        latency_str = f"{latency:.0f}ms"

    # Get node info
    node_info = ""
    if run.extra and 'metadata' in run.extra:
        meta = run.extra['metadata']
        if 'node_type' in meta:
            node_info = f" [{meta['node_type']}]"

    print(f"{indent}├─ {run.name}{node_info} ({latency_str})")

    # Get children
    try:
        children = list(client.list_runs(
            project_name=project_name,
            filter=f'eq(parent_run_id, "{run.id}")',
            limit=10
        ))

        for child in children:
            _print_tree(client, project_name, child, level + 1, max_level)
    except:
        pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "detail" and len(sys.argv) > 2:
            # Get detailed view of specific run
            get_detailed_run(sys.argv[2])
        else:
            # Analyze with custom limit
            analyze_traces(limit=int(sys.argv[1]))
    else:
        # Default analysis
        analyze_traces(limit=5)

    print("\n✨ Analysis complete!")
    print("\nUsage:")
    print("  python analyze_langsmith_traces.py        # Analyze last 5 runs")
    print("  python analyze_langsmith_traces.py 20     # Analyze last 20 runs")
    print("  python analyze_langsmith_traces.py detail <run_id>  # Detailed view of specific run")