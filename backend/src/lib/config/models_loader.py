"""Model catalog loader with package-default and runtime-override merging.

Model versions: a catalog entry may list the ids it ``replaces``. Each replaced id
resolves to that entry everywhere (``get_model``, ``resolve_model_id``), so moving
to a new model version is one catalog edit and nothing saved is changed: saved
agents, pinned revisions and environment values keep the id they name and run on
the entry that replaces it. The owner's decision is recorded in ALL-1708. The
mapping is flat (a replaced id maps directly to a current entry) and an id the
catalog does not know is still refused.
"""

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
    supports_tool_search: bool = False
    reasoning_options: List[str] = field(default_factory=list)
    default_reasoning: Optional[str] = None
    reasoning_descriptions: Dict[str, str] = field(default_factory=dict)
    recommended_for: List[str] = field(default_factory=list)
    avoid_for: List[str] = field(default_factory=list)
    source_label: Optional[str] = None
    context_window_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    # Model ids this entry replaces; each resolves to this entry (ALL-1708).
    replaces: List[str] = field(default_factory=list)
    # Reviewed mapping for saved reasoning levels this entry does not offer.
    reasoning_map: Dict[str, str] = field(default_factory=dict)

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
        supports_tool_search = data.get("supports_tool_search", False)
        if not isinstance(supports_tool_search, bool):
            raise ValueError(
                f"Model entry '{model_id}' in {source_label} field "
                "'supports_tool_search' must be a boolean"
            )
        reasoning_descriptions = _parse_string_map(
            data.get("reasoning_descriptions"),
            field_name=f"{model_id}.reasoning_descriptions",
            source_label=source_label,
            normalize_key_lower=True,
        )
        replaces = _parse_string_list(
            data.get("replaces"),
            field_name=f"{model_id}.replaces",
            source_label=source_label,
        )
        if model_id in replaces:
            raise ValueError(f"Model entry '{model_id}' in {source_label} replaces itself")
        reasoning_map = {
            key: value.lower()
            for key, value in _parse_string_map(
                data.get("reasoning_map"),
                field_name=f"{model_id}.reasoning_map",
                source_label=source_label,
                normalize_key_lower=True,
            ).items()
        }
        for saved_level, level in reasoning_map.items():
            if saved_level in reasoning_options or level not in reasoning_options:
                raise ValueError(
                    f"Model entry '{model_id}' in {source_label} reasoning_map "
                    f"'{saved_level}: {level}' must map a level this model does not offer "
                    "to one it offers"
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
            supports_tool_search=supports_tool_search,
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
            replaces=replaces,
            reasoning_map=reasoning_map,
            **{key: _capacity_value(data.get(key), key, model_id) for key in
                ("context_window_tokens", "max_input_tokens", "max_output_tokens")},
        )


def _capacity_value(value, key, model_id):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"Model {model_id} {key} must be a positive integer")
    return value


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
                earlier = registry.get(model.model_id)
                if earlier is not None and earlier.replaces != model.replaces:
                    # An override that differs only in replaces would silently decide which
                    # saved ids still run (ALL-1708): the two files must agree.
                    raise ModelCatalogError(
                        f"Model '{model.model_id}' replaces {earlier.replaces} in "
                        f"{earlier.source_label} but {model.replaces} in {model.source_label}"
                    )
                registry[model.model_id] = model

        _check_replacements(registry)
        _model_registry = registry
        _initialized = True
        logger.info("Loaded %s model definitions", len(_model_registry))
        return _model_registry


class ModelCatalogError(ValueError):
    """The merged model catalog is inconsistent; startup stops."""


def _check_replacements(registry: Dict[str, ModelDefinition]) -> None:
    owners: Dict[str, str] = {}
    for model in registry.values():
        for replaced in model.replaces:
            if replaced in registry:
                raise ModelCatalogError(
                    f"Model id '{replaced}' is both a catalog entry and replaced by "
                    f"'{model.model_id}'"
                )
            if replaced in owners:
                raise ModelCatalogError(
                    f"Model id '{replaced}' is replaced by both '{owners[replaced]}' and "
                    f"'{model.model_id}'"
                )
            owners[replaced] = model.model_id


def _replacing_entry(model_id: str) -> Optional[ModelDefinition]:
    for model in _model_registry.values():
        if model_id in model.replaces:
            return model
    return None


def get_model(model_id: str) -> Optional[ModelDefinition]:
    """Get the catalog entry for an id; a replaced id gets the entry replacing it."""
    if not _initialized:
        load_models()
    return _model_registry.get(model_id) or _replacing_entry(model_id)


def resolve_model_id(model_id: str) -> str:
    """The catalog id actually requested for a configured id (ALL-1708).

    A replaced id becomes the id of the entry replacing it; any other id is returned
    unchanged, so an id the catalog does not know is refused where it is checked."""
    if not _initialized:
        load_models()
    if model_id in _model_registry:
        return model_id
    replacing = _replacing_entry(model_id)
    return replacing.model_id if replacing is not None else model_id


def is_replaced_model_id(model_id: str) -> bool:
    """True when the catalog lists ``model_id`` in some entry's ``replaces``."""
    if not _initialized:
        load_models()
    return model_id not in _model_registry and _replacing_entry(model_id) is not None


def index_models(models: List[ModelDefinition]) -> Dict[str, Any]:
    """``{id: entry}`` for these entries and every id they replace (ALL-1708)."""
    index: Dict[str, Any] = {model.model_id: model for model in models}
    for model in models:
        for replaced in getattr(model, "replaces", ()):
            index[replaced] = model
    return index


def model_index() -> Dict[str, ModelDefinition]:
    """Every id the catalog resolves (entries and replaced ids) to its entry."""
    return index_models(list_models())


def get_default_model() -> Optional[ModelDefinition]:
    """Get configured default model, or first defined model."""
    if not _initialized:
        load_models()
    for model in _model_registry.values():
        if model.default:
            return model
    return next(iter(_model_registry.values()), None)


def is_model_selectable(model: ModelDefinition) -> bool:
    """Shared curator catalog policy, including assistant authoring surfaces."""
    from src.lib.openai_agents.config import is_provider_enabled

    return bool(getattr(model, "curator_visible", True)) and is_provider_enabled(
        getattr(model, "provider", "openai")
    )


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
