"""Startup check that deployment model settings name catalog models.

A retired model left in ``.env`` otherwise fails late and quietly: PDF
section hierarchy comes out empty, figure locator resolution fails on every
upload, and agent overrides only surface after system agent sync. Every set
variable below must name a model in the catalog, and every set reasoning
variable must be a level that model offers.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Tuple

from .models_loader import get_model

# (model env var, reasoning env var or None) for single-purpose settings.
MODEL_ENV_SETTINGS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("DEFAULT_AGENT_MODEL", "DEFAULT_AGENT_REASONING"),
    ("SUPERVISOR_MODEL", None),
    ("HIERARCHY_LLM_MODEL", "HIERARCHY_LLM_REASONING"),
    ("FIGURE_LOCATOR_LLM_MODEL", "FIGURE_LOCATOR_LLM_REASONING"),
    ("ABSTRACT_EXTRACTION_MODEL", "ABSTRACT_EXTRACTION_REASONING"),
    ("AGENT_STUDIO_OPENAI_MODEL", "AGENT_STUDIO_REASONING_EFFORT"),
    ("BENCHMARK_ADJUDICATION_MODEL", None),
)

# Per-agent overrides: AGENT_<NAME>_MODEL with an optional AGENT_<NAME>_REASONING.
_AGENT_MODEL_ENV = re.compile(r"^AGENT_([A-Z0-9_]+)_MODEL$")


def _model_env_settings(environ: dict[str, str]) -> Iterable[Tuple[str, Optional[str]]]:
    explicit = {model_var for model_var, _ in MODEL_ENV_SETTINGS}
    yield from MODEL_ENV_SETTINGS
    for name in sorted(environ):
        match = _AGENT_MODEL_ENV.match(name)
        if match and name not in explicit:
            yield name, f"AGENT_{match.group(1)}_REASONING"


def model_env_errors(environ: Optional[dict[str, str]] = None) -> List[str]:
    """Return one message per set model or reasoning variable the catalog rejects."""
    env = dict(os.environ if environ is None else environ)
    errors: List[str] = []
    for model_var, reasoning_var in _model_env_settings(env):
        model_id = (env.get(model_var) or "").strip()
        if not model_id:
            continue
        model = get_model(model_id)
        if model is None:
            errors.append(f"{model_var}='{model_id}' is not a model in the model catalog")
            continue
        reasoning = (env.get(reasoning_var) or "").strip().lower() if reasoning_var else ""
        if reasoning and (
            not model.supports_reasoning or reasoning not in model.reasoning_options
        ):
            allowed = ", ".join(model.reasoning_options) or "none"
            errors.append(
                f"{reasoning_var}='{reasoning}' is not a reasoning level of {model_var} "
                f"model '{model_id}' (allowed: {allowed})"
            )
    return errors


def validate_model_env() -> None:
    """Fail startup when a deployment model setting names a model or level the catalog lacks."""
    errors = model_env_errors()
    if errors:
        raise RuntimeError("Model settings in the environment are invalid: " + "; ".join(errors))
