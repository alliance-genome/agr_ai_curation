"""Per-agent prompt-layer size report (no DB required: core layers only)."""
from __future__ import annotations

from src.lib.config.agent_loader import (
    canonical_system_agent_key,
    load_agent_definitions,
)
from src.lib.prompts.assembly import build_agent_core_prompt


def core_layer_sizes() -> dict[str, dict[str, int]]:
    """Return {agent_id: {layer_kind: char_count, "total": int}} for core layers."""
    report: dict[str, dict[str, int]] = {}
    for agent in load_agent_definitions().values():
        agent_id = canonical_system_agent_key(agent)
        try:
            bundle = build_agent_core_prompt(agent_id)
        except ValueError:
            # Agent has no resolvable core-generated contract (e.g. unregistered
            # output schema); skip it rather than masking real assembly errors.
            continue
        sizes = {layer.kind: len(layer.content) for layer in bundle.layers}
        sizes["total"] = sum(len(layer.content) for layer in bundle.layers)
        report[agent_id] = sizes
    return report


def format_report(report: dict[str, dict[str, int]]) -> str:
    """Render the report as a stable, sorted text table."""
    lines = [f"{'agent_id':40s} {'core_static':>11} {'core_generated':>14} {'total':>7}"]
    for agent_id in sorted(report):
        s = report[agent_id]
        lines.append(
            f"{agent_id:40s} {s.get('core_static', 0):11d} "
            f"{s.get('core_generated', 0):14d} {s.get('total', 0):7d}"
        )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - thin diagnostic entry point
    print(format_report(core_layer_sizes()))
