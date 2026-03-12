"""Model catalog loader with package-default and runtime-override merging."""

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.lib.packages import ExportKind

from .package_default_sources import (
    load_optional_runtime_yaml_source,
    load_package_yaml_sources,
)

logger = logging.getLogger(__name__)
_init_lock = threading.Lock()


@dataclass
class ModelDefinition:
    """Curator-visible model definition."""

    model_id: str
    name: str
    provider: str
    description: str = ""
    guidance: str = ""
    default: bool = False
    curator_visible: bool = True
    supports_reasoning: bool = True
    supports_temperature: bool = True
    reasoning_options: List[str] = field(default_factory=list)
    default_reasoning: Optional[str] = None
    reasoning_descriptions: Dict[str, str] = field(default_factory=dict)
    recommended_for: List[str] = field(default_factory=list)
    avoid_for: List[str] = field(default_factory=list)
    source_label: Optional[str] = None

    @classmethod
    def from_yaml(
        cls,
        data: Dict[str, Any],
        *,
        source_label: str,
    ) -> "ModelDefinition":
        model_id = str(data.get("model_id", "")).strip()
        if not model_id:
            raise ValueError(
                f"Model entry in {source_label} is missing required field 'model_id'"
            )

        name = str(data.get("name", model_id)).strip() or model_id
        provider = str(data.get("provider", "openai")).strip() or "openai"
        reasoning_options = _parse_string_list(
            data.get("reasoning_options"),
            field_name=f"{model_id}.reasoning_options",
            source_label=source_label,
            normalize_lower=True,
        )
        default_reasoning = str(data.get("default_reasoning", "")).strip().lower() or None
        if default_reasoning and reasoning_options and default_reasoning not in reasoning_options:
            raise ValueError(
                f"Model entry '{model_id}' in {source_label} has "
                f"default_reasoning='{default_reasoning}' "
                f"which is not in reasoning_options"
            )
        reasoning_descriptions = _parse_string_map(
            data.get("reasoning_descriptions"),
            field_name=f"{model_id}.reasoning_descriptions",
            source_label=source_label,
            normalize_key_lower=True,
        )

        return cls(
            model_id=model_id,
            name=name,
            provider=provider,
            description=str(data.get("description", "")).strip(),
            guidance=str(data.get("guidance", data.get("description", ""))).strip(),
            default=bool(data.get("default", False)),
            curator_visible=bool(data.get("curator_visible", True)),
            supports_reasoning=bool(data.get("supports_reasoning", True)),
            supports_temperature=bool(data.get("supports_temperature", True)),
            reasoning_options=reasoning_options,
            default_reasoning=default_reasoning,
            reasoning_descriptions=reasoning_descriptions,
            recommended_for=_parse_string_list(
                data.get("recommended_for"),
                field_name=f"{model_id}.recommended_for",
                source_label=source_label,
            ),
            avoid_for=_parse_string_list(
                data.get("avoid_for"),
                field_name=f"{model_id}.avoid_for",
                source_label=source_label,
            ),
            source_label=source_label,
        )


def _parse_string_list(
    raw: Any,
    *,
    field_name: str,
    source_label: str,
    normalize_lower: bool = False,
) -> List[str]:
    """Parse optional YAML list fields into cleaned string lists."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"Model entry in {source_label} field '{field_name}' must be a list"
        )

    values: List[str] = []
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        if normalize_lower:
            value = value.lower()
        values.append(value)
    return values


def _parse_string_map(
    raw: Any,
    *,
    field_name: str,
    source_label: str,
    normalize_key_lower: bool = False,
) -> Dict[str, str]:
    """Parse optional YAML map fields into cleaned string maps."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Model entry in {source_label} field '{field_name}' must be a mapping"
        )

    values: Dict[str, str] = {}
    for key, value in raw.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not clean_key or not clean_value:
            continue
        if normalize_key_lower:
            clean_key = clean_key.lower()
        values[clean_key] = clean_value
    return values


_model_registry: Dict[str, ModelDefinition] = {}
_initialized = False


def load_models(
    models_path: Optional[Path] = None,
    *,
    packages_dir: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, ModelDefinition]:
    """Load models catalog from package defaults plus runtime overrides."""
    global _model_registry, _initialized

    with _init_lock:
        if _initialized and not force_reload:
            return _model_registry

        sources = list(
            load_package_yaml_sources(
                export_kind=ExportKind.MODEL,
                packages_dir=packages_dir,
            )
        )
        runtime_source = load_optional_runtime_yaml_source(
            explicit_path=models_path,
            env_var="MODELS_CONFIG_PATH",
            filename="models.yaml",
        )
        if runtime_source is not None:
            sources.append(runtime_source)

        if not sources:
            raise FileNotFoundError(
                "No model defaults were found in runtime packages or runtime override config"
            )

        registry: Dict[str, ModelDefinition] = {}
        for source in sources:
            entries = source.payload.get("models")
            if not isinstance(entries, list):
                raise ValueError(
                    f"{source.describe()} must define a top-level 'models' list"
                )

            for raw in entries:
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"Each model entry in {source.describe()} must be a mapping"
                    )
                model = ModelDefinition.from_yaml(raw, source_label=source.describe())
                registry[model.model_id] = model

        _model_registry = registry
        _initialized = True
        logger.info("Loaded %s model definitions", len(_model_registry))
        return _model_registry


def get_model(model_id: str) -> Optional[ModelDefinition]:
    """Get one model definition by ID."""
    if not _initialized:
        load_models()
    return _model_registry.get(model_id)


def get_default_model() -> Optional[ModelDefinition]:
    """Get configured default model, or first defined model."""
    if not _initialized:
        load_models()
    for model in _model_registry.values():
        if model.default:
            return model
    return next(iter(_model_registry.values()), None)


def list_models() -> List[ModelDefinition]:
    """List all model definitions."""
    if not _initialized:
        load_models()
    return list(_model_registry.values())


def is_initialized() -> bool:
    """Check if model registry has been loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset cached model definitions (tests)."""
    global _model_registry, _initialized
    with _init_lock:
        _model_registry = {}
        _initialized = False
