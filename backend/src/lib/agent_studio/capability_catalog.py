"""Authenticated, bounded capability discovery for Agent Studio authoring.

The catalog is an adapter over the same live services used by the human Agent
Studio UI.  It deliberately owns no persistence and grants no durable access:
every page/detail request rebuilds the authorized view for the current caller.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.agent_service import list_agents_visible_to_user
from src.lib.agent_studio.catalog_service import (
    AGENT_REGISTRY,
    get_tool_details,
    has_tool_binding,
    tool_requires_document,
)
from src.lib.agent_studio.flow_agent_policy import flow_palette_show_in_palette
from src.lib.agent_studio.domain_output_contract import domain_extraction_ref_for_agent
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.config import list_groups, list_model_definitions
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.group_tool_policy import resolve_group_tool_policy
from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    substring_match,
)
from src.lib.openai_agents.config import (
    get_agent_studio_capability_catalog_max_records,
    get_agent_studio_provider_tool_result_inline_max_chars,
    get_tool_page_default_limit,
    get_tool_page_max_limit,
)


CAPABILITY_KINDS = (
    "agent",
    "flow_template",
    "group",
    "model",
    "output_contract",
    "profile",
    "tool",
    "validator_capability",
)
class CapabilityCatalogRequestError(ValueError):
    """Expected invalid/stale catalog request; not an infrastructure crash."""


class CapabilityCatalogUnavailable(RuntimeError):
    """Catalog construction failed at an actionable application boundary."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        candidate_count: int = 0,
        bound: int | None = None,
        catalog_fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.candidate_count = candidate_count
        self.bound = bound
        self.catalog_fingerprint = catalog_fingerprint

    def sanitized_context(self) -> dict[str, Any]:
        return {
            "authorization_phase": self.phase,
            "candidate_count": self.candidate_count,
            "bound": self.bound,
            "bound_exceeded": bool(
                self.bound is not None and self.candidate_count > self.bound
            ),
            "catalog_fingerprint": self.catalog_fingerprint,
        }


@dataclass(frozen=True)
class CapabilityCatalogContext:
    """Request-local identity and authoring scope used to compile the catalog."""

    user_id: int
    active_group_ids: tuple[str, ...] = ()
    active_tab: str = "agents"
    artifact_kind: str = "agent"


@dataclass(frozen=True)
class CapabilityRecord:
    """One authorized non-callable resource and its safe exact detail."""

    kind: str
    resource_id: str
    name: str
    description: str
    availability: str = "available"
    selectable: bool = True
    authorization_scope: str = "configured"
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "resource_id": self.resource_id,
            "name": self.name,
            "description": self.description,
            "availability": self.availability,
            "selectable": self.selectable,
            "authorization_scope": self.authorization_scope,
            "compatibility": dict(self.compatibility),
            "detail_call": {
                "tool": "get_studio_capability_detail",
                "arguments": {
                    "kind": self.kind,
                    "resource_id": self.resource_id,
                },
            },
        }

    def exact_detail(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "detail": dict(self.detail),
        }


class CapabilityCatalogExtension(Protocol):
    """Stable adapter boundary for later profile/validator catalog providers."""

    def list_capabilities(
        self,
        *,
        db: Session,
        context: CapabilityCatalogContext,
    ) -> Sequence[CapabilityRecord]: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _authorization_scope(agent: Any, user_id: int) -> str:
    visibility = str(getattr(agent, "visibility", "") or "").strip()
    if visibility == "system":
        return "system"
    if getattr(agent, "user_id", None) == user_id:
        return "owned"
    return "shared"


def _domain_envelope_detail(agent: Any) -> dict[str, Any] | None:
    """Project safe maturity/operation facts from the agent's domain package."""

    agent_key = str(getattr(agent, "agent_key", "") or "")
    source_key = str(getattr(agent, "template_source", "") or "")
    entry = AGENT_REGISTRY.get(agent_key) or AGENT_REGISTRY.get(source_key)
    if not isinstance(entry, Mapping):
        return None
    curation = entry.get("curation")
    if not isinstance(curation, Mapping):
        return None
    domain_pack_id = str(curation.get("domain_pack_id") or "").strip()
    if not domain_pack_id:
        return None

    from src.lib.flows.validation_attachments import domain_pack_validation_registries

    registry = domain_pack_validation_registries().get(domain_pack_id)
    if registry is None:
        return {
            "domain_pack_id": domain_pack_id,
            "availability": "unavailable",
        }
    metadata = registry.domain_pack.metadata
    operation_limitations: list[dict[str, Any]] = []
    for key, raw in sorted(metadata.metadata.items()):
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "").strip()
        if not status or not any(
            marker in str(key).lower()
            for marker in ("behavior", "operation", "policy")
        ):
            continue
        operation_limitations.append(
            {
                "operation": str(key),
                "status": status,
                "reason": str(raw.get("reason") or "").strip() or None,
                "blocked_operations": list(raw.get("blocked_operations") or []),
            }
        )
    return {
        "domain_pack_id": domain_pack_id,
        "domain_pack_version": metadata.version,
        "maturity": metadata.status.value,
        "launchable": bool(curation.get("launchable")),
        "operation_limitations": operation_limitations,
    }


def _agent_records(
    db: Session,
    context: CapabilityCatalogContext,
) -> list[CapabilityRecord]:
    records: list[CapabilityRecord] = []
    agents = list_agents_visible_to_user(
        db,
        context.user_id,
        context.active_group_ids,
    )
    for agent in agents:
        agent_id = str(agent.agent_key)
        category = str(getattr(agent, "category", "") or "Custom")
        supervisor = {"enabled": bool(getattr(agent, "supervisor_enabled", False))}
        policy_entry = {
            "category": category,
            "supervisor": supervisor,
            "frontend": {
                "show_in_palette": bool(getattr(agent, "show_in_palette", True)),
            },
        }
        flow_selectable = flow_palette_show_in_palette(agent_id, policy_entry)
        tool_resolution = resolve_group_tool_policy(
            list(getattr(agent, "tool_ids", None) or []),
            dict(getattr(agent, "group_tool_policy", None) or {}),
            list(context.active_group_ids),
        )
        effective_tool_ids = list(tool_resolution.tool_ids)
        domain_envelope = _domain_envelope_detail(agent)
        domain_ref = (
            domain_extraction_ref_for_agent(agent_id, active_group_ids=list(context.active_group_ids))
            if agent.visibility == "system" else None
        )
        output_schema_key = str(
            getattr(agent, "output_schema_key", "") or ""
        ).strip() or None
        compatibility = {
            "active_tab": context.active_tab,
            "artifact_kind": context.artifact_kind,
            "flow_selectable": flow_selectable,
            "requires_document": any(
                tool_requires_document(tool_id) for tool_id in effective_tool_ids
            ),
            "category": category,
        }
        records.append(
            CapabilityRecord(
                kind="agent",
                resource_id=agent_id,
                name=str(agent.name),
                description=str(getattr(agent, "description", "") or ""),
                authorization_scope=_authorization_scope(agent, context.user_id),
                selectable=True,
                compatibility=compatibility,
                detail={
                    "agent_id": agent_id,
                    "identity_contract": {
                        "phase": "saved_agent_id",
                        "agent_revision_id": None,
                        "profile_revision_id": None,
                    },
                    "visibility": str(agent.visibility),
                    "allowed_group_ids": list(agent.allowed_group_ids or []),
                    "model_id": str(agent.model_id),
                    "model_reasoning": getattr(agent, "model_reasoning", None),
                    "tool_ids": effective_tool_ids,
                    "output_schema_key": output_schema_key,
                    "version": int(getattr(agent, "version", 1) or 1),
                    "updated_at": getattr(agent, "updated_at", None),
                    "domain_envelope": domain_envelope,
                    "domain_extraction_ref": domain_ref.model_dump(mode="json") if domain_ref else None,
                },
            )
        )
    return records


def _model_records(context: CapabilityCatalogContext) -> list[CapabilityRecord]:
    records: list[CapabilityRecord] = []
    for model in list_model_definitions():
        if not bool(getattr(model, "curator_visible", True)):
            continue
        reasoning_options = list(model.reasoning_options or [])
        records.append(
            CapabilityRecord(
                kind="model",
                resource_id=model.model_id,
                name=model.name,
                description=model.description,
                compatibility={
                    "active_tab": context.active_tab,
                    "artifact_kind": context.artifact_kind,
                    "supports_reasoning": bool(model.supports_reasoning),
                    "supports_temperature": bool(model.supports_temperature),
                },
                detail={
                    "model_id": model.model_id,
                    "provider": model.provider,
                    "guidance": model.guidance,
                    "default": bool(model.default),
                    "reasoning_options": reasoning_options,
                    "default_reasoning": model.default_reasoning,
                    "reasoning_descriptions": dict(
                        model.reasoning_descriptions or {}
                    ),
                    "recommended_for": list(model.recommended_for or []),
                    "avoid_for": list(model.avoid_for or []),
                },
            )
        )
    return records


def _tool_schema_summary(tool_key: str) -> list[dict[str, Any]]:
    details = get_tool_details(tool_key) or {}
    documentation = details.get("documentation")
    raw_parameters: Any = None
    if isinstance(documentation, Mapping):
        raw_parameters = documentation.get("parameters")
    if raw_parameters is None:
        raw_parameters = details.get("parameters")
    if isinstance(raw_parameters, Mapping):
        raw_parameters = [
            {"name": name, **(dict(value) if isinstance(value, Mapping) else {})}
            for name, value in raw_parameters.items()
        ]
    if not isinstance(raw_parameters, list):
        return []
    summaries: list[dict[str, Any]] = []
    for parameter in raw_parameters:
        if not isinstance(parameter, Mapping):
            continue
        name = str(parameter.get("name") or "").strip()
        if not name:
            continue
        summaries.append(
            {
                "name": name,
                "type": str(parameter.get("type") or "any"),
                "required": bool(parameter.get("required", False)),
                "description": str(parameter.get("description") or ""),
            }
        )
    return summaries


def _tool_records(
    db: Session,
    context: CapabilityCatalogContext,
) -> list[CapabilityRecord]:
    records: list[CapabilityRecord] = []
    # Library caching is not an authorization snapshot: re-read revocations on
    # every model-facing search/detail and proposal validation.
    for entry in get_tool_policy_cache().refresh(db):
        if not entry.curator_visible:
            continue
        raw_allowed_groups = entry.config.get("allowed_group_ids", [])
        allowed_groups = (
            list(raw_allowed_groups) if isinstance(raw_allowed_groups, list) else []
        )
        if not is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=allowed_groups,
            active_group_ids=list(context.active_group_ids),
            resource_kind="agent_studio_tool",
        ):
            continue
        installed = has_tool_binding(entry.tool_key)
        selectable = bool(installed and (entry.allow_attach or entry.allow_execute))
        availability = "available" if selectable else (
            "unavailable" if not installed else "blocked"
        )
        records.append(
            CapabilityRecord(
                kind="tool",
                resource_id=entry.tool_key,
                name=entry.display_name,
                description=entry.description,
                availability=availability,
                selectable=selectable,
                compatibility={
                    "active_tab": context.active_tab,
                    "artifact_kind": context.artifact_kind,
                    "attachable": bool(entry.allow_attach),
                    "executable": bool(entry.allow_execute),
                    "requires_document": tool_requires_document(entry.tool_key),
                    "applicable_artifact_kinds": ["agent", "flow_agent"],
                },
                detail={
                    "tool_id": entry.tool_key,
                    "category": entry.category,
                    "curator_visible": bool(entry.curator_visible),
                    "allow_attach": bool(entry.allow_attach),
                    "allow_execute": bool(entry.allow_execute),
                    "requires_document": tool_requires_document(entry.tool_key),
                    "installed_binding": installed,
                    "input_schema_summary": _tool_schema_summary(entry.tool_key),
                    "policy": dict(entry.config),
                },
            )
        )
    return records


def _output_contract_records(
    agents: Sequence[CapabilityRecord],
    context: CapabilityCatalogContext,
) -> list[CapabilityRecord]:
    records = [
        CapabilityRecord(
            kind="output_contract",
            resource_id="none",
            name="No structured output",
            description=(
                "Return an ordinary assistant response without selecting a structured "
                "output schema. This is distinct from a generic profile or envelope."
            ),
            compatibility={
                "active_tab": context.active_tab,
                "artifact_kind": context.artifact_kind,
                "contract_kind": "none",
            },
            detail={
                "output_schema_key": None,
                "contract_kind": "none",
                "output_contract": {"output_state": "none"},
                "unprofiled_generic": False,
                "operation_limitations": [],
            },
        )
    ]
    by_schema: dict[str, list[CapabilityRecord]] = {}
    for agent in agents:
        domain_ref = agent.detail.get("domain_extraction_ref")
        if domain_ref is not None:
            records.append(CapabilityRecord(
                kind="output_contract",
                resource_id=f"builder:{domain_ref['package_id']}:{domain_ref['agent_id']}",
                name=agent.name,
                description=agent.description,
                authorization_scope=agent.authorization_scope,
                compatibility={"active_tab": context.active_tab, "artifact_kind": context.artifact_kind,
                               "contract_kind": "packaged_builder"},
                detail={
                    "contract_kind": "packaged_builder", "output_schema_key": None,
                    "domain_extraction_ref": domain_ref,
                    "output_contract": {"output_state": "structured_extraction", "output_mode": "domain",
                                        "output_schema_key": None, "domain_extraction_ref": domain_ref},
                    "provider_agent_ids": [agent.resource_id],
                    "domain_envelopes": [agent.detail["domain_envelope"]] if agent.detail.get("domain_envelope") else [],
                    "selection_requirements": [
                        "Keep the selected package's matching builder finalizer and access/tool policy. "
                        "Choosing this format does not grant tools. Start from its agent template when needed.",
                        "Model output schema must remain null; the backend materializes this envelope.",
                    ],
                },
            ))
        schema_key = str(agent.detail.get("output_schema_key") or "").strip()
        if schema_key:
            by_schema.setdefault(schema_key, []).append(agent)

    for schema_key, providers in sorted(by_schema.items()):
        schema = resolve_output_schema(schema_key)
        if schema is None:
            continue
        envelope_facts = [
            provider.detail.get("domain_envelope")
            for provider in providers
            if provider.detail.get("domain_envelope") is not None
        ]
        description = (schema.__doc__ or "").strip().split("\n", 1)[0]
        records.append(
            CapabilityRecord(
                kind="output_contract",
                resource_id=schema_key,
                name=schema_key,
                description=description or f"Structured output contract {schema_key}",
                compatibility={
                    "active_tab": context.active_tab,
                    "artifact_kind": context.artifact_kind,
                    "contract_kind": "registered_schema",
                    "provider_agent_count": len(providers),
                },
                detail={
                    "output_schema_key": schema_key,
                    "contract_kind": "registered_schema",
                    "output_contract": {"output_state": "structured_extraction", "output_mode": "domain", "output_schema_key": schema_key},
                    "unprofiled_generic": False,
                    "provider_agent_ids": [provider.resource_id for provider in providers],
                    "selection_requirements": [
                        "A model-response schema cannot be combined with builder-finalization tools."
                    ],
                    "domain_envelopes": envelope_facts,
                    "json_schema": schema.model_json_schema(),
                },
            )
        )
    return records


def _group_records(context: CapabilityCatalogContext) -> list[CapabilityRecord]:
    return [
        CapabilityRecord(
            kind="group",
            resource_id=group.group_id,
            name=group.name,
            description=group.description,
            authorization_scope="configured_workshop_option",
            compatibility={
                "active_tab": context.active_tab,
                "artifact_kind": context.artifact_kind,
                "workshop_selectable": True,
                "inherited_access_floor": False,
            },
            detail={
                "group_id": group.group_id,
                "name": group.name,
                "description": group.description,
                "species": group.species,
                "taxon": group.taxon,
                "selection_semantics": "authoritative_human_workshop_option",
            },
        )
        for group in list_groups()
    ]


def _flow_template_records(
    agents: Sequence[CapabilityRecord],
    context: CapabilityCatalogContext,
) -> list[CapabilityRecord]:
    from src.lib.agent_studio.flow_tools import list_available_flow_templates

    available_agent_ids = {
        agent.resource_id
        for agent in agents
        if bool(agent.compatibility.get("flow_selectable"))
    }
    templates = list_available_flow_templates(
        available_agent_ids=available_agent_ids,
        active_group_ids=list(context.active_group_ids),
    )
    return [
        CapabilityRecord(
            kind="flow_template",
            resource_id=str(template["name"]),
            name=str(template["name"]),
            description=str(template.get("description") or ""),
            compatibility={
                "active_tab": context.active_tab,
                "artifact_kind": context.artifact_kind,
                "step_count": len(template.get("steps") or []),
            },
            detail={
                "template_id": str(template["name"]),
                "allowed_group_ids": list(template.get("allowed_group_ids") or []),
                "steps": list(template.get("steps") or []),
            },
        )
        for template in templates
    ]


def build_authorized_capability_catalog(
    *,
    db: Session,
    context: CapabilityCatalogContext,
    extensions: Sequence[CapabilityCatalogExtension] = (),
) -> list[CapabilityRecord]:
    """Compile one deterministic request-local catalog from authoritative sources."""

    if not isinstance(context.user_id, int) or isinstance(context.user_id, bool):
        raise CapabilityCatalogUnavailable(
            "Authenticated database identity is required for capability discovery",
            phase="identity",
        )

    agents = _agent_records(db, context)
    records = [
        *agents,
        *_model_records(context),
        *_tool_records(db, context),
        *_output_contract_records(agents, context),
        *_flow_template_records(agents, context),
        *_group_records(context),
    ]
    for extension in extensions:
        records.extend(extension.list_capabilities(db=db, context=context))

    normalized: list[CapabilityRecord] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.kind, record.resource_id)
        if record.kind not in CAPABILITY_KINDS:
            raise CapabilityCatalogUnavailable(
                "Capability extension returned an unsupported resource kind",
                phase="extension",
                candidate_count=len(records),
            )
        if not record.resource_id.strip() or key in seen:
            raise CapabilityCatalogUnavailable(
                "Capability catalog contains an invalid or duplicate stable identity",
                phase="compile",
                candidate_count=len(records),
            )
        seen.add(key)
        normalized.append(record)

    normalized.sort(key=lambda item: (item.kind, item.resource_id.casefold(), item.resource_id))
    maximum = get_agent_studio_capability_catalog_max_records()
    fingerprint = capability_catalog_fingerprint(normalized)
    if len(normalized) > maximum:
        raise CapabilityCatalogUnavailable(
            "Authorized capability catalog exceeds its configured bound",
            phase="bound",
            candidate_count=len(normalized),
            bound=maximum,
            catalog_fingerprint=fingerprint,
        )
    return normalized


def capability_catalog_fingerprint(records: Sequence[CapabilityRecord]) -> str:
    return _sha256(_canonical_json([record.summary() for record in records]))


def _normalized_kinds(kinds: Sequence[str] | None) -> set[str]:
    if not kinds:
        return set(CAPABILITY_KINDS)
    normalized = {str(kind or "").strip() for kind in kinds}
    unsupported = sorted(normalized - set(CAPABILITY_KINDS))
    if unsupported:
        raise CapabilityCatalogRequestError(
            "Unsupported capability kind(s): " + ", ".join(unsupported)
        )
    return normalized


def _provider_chars(value: Any) -> int:
    return len(json.dumps(value, default=str))


def search_capabilities(
    *,
    db: Session,
    context: CapabilityCatalogContext,
    query: str | None = None,
    kinds: Sequence[str] | None = None,
    cursor: str | int | None = None,
    limit: int | None = None,
    catalog_fingerprint: str | None = None,
    extensions: Sequence[CapabilityCatalogExtension] = (),
) -> dict[str, Any]:
    """Return one provider-bounded page of authorized capability summaries."""

    records = build_authorized_capability_catalog(
        db=db,
        context=context,
        extensions=extensions,
    )
    fingerprint = capability_catalog_fingerprint(records)
    if catalog_fingerprint is not None and catalog_fingerprint != fingerprint:
        raise CapabilityCatalogRequestError(
            "The authorized capability catalog changed. Search again from the first page."
        )
    selected_kinds = _normalized_kinds(kinds)
    matched = [
        record
        for record in records
        if record.kind in selected_kinds
        and substring_match(
            query,
            record.kind,
            record.resource_id,
            record.name,
            record.description,
            _canonical_json(record.compatibility),
        )
    ]
    bounded_limit = normalize_page_limit(
        limit,
        default=get_tool_page_default_limit(),
        maximum=get_tool_page_max_limit(),
    )
    offset = parse_offset_cursor(cursor)
    if offset and catalog_fingerprint is None:
        raise CapabilityCatalogRequestError(
            "catalog_fingerprint is required when continuing a capability search."
        )
    page, _, _ = offset_page(matched, limit=bounded_limit, cursor=offset)
    summaries = [record.summary() for record in page]

    def build_response() -> dict[str, Any]:
        next_offset = offset + len(summaries)
        has_more = next_offset < len(matched)
        next_call = None
        if has_more:
            next_call = {
                "tool": "search_studio_capabilities",
                "arguments": {
                    **({"query": query} if str(query or "").strip() else {}),
                    "kinds": sorted(selected_kinds),
                    "cursor": str(next_offset),
                    "limit": bounded_limit,
                    "catalog_fingerprint": fingerprint,
                },
            }
        response = {
            "success": True,
            "catalog_fingerprint": fingerprint,
            "results": summaries,
            "total_count": len(matched),
            "returned_count": len(summaries),
            "truncated": has_more,
            "next_cursor": str(next_offset) if has_more else None,
            "next_call": next_call,
            "query": str(query or "").strip() or None,
            "kinds": sorted(selected_kinds),
            "authorization": "request_local",
            "instruction": (
                "Use only returned stable IDs. Search results are descriptive, not "
                "authorization grants; detail and later mutation reauthorize them."
            ),
        }
        for summary in response["results"]:
            summary["detail_call"]["arguments"]["catalog_fingerprint"] = fingerprint
        return response

    response = build_response()
    maximum_chars = get_agent_studio_provider_tool_result_inline_max_chars()
    while _provider_chars(response) > maximum_chars and summaries:
        summaries.pop()
        response = build_response()
    if not summaries and offset < len(matched):
        raise CapabilityCatalogUnavailable(
            "No capability summary fits the provider result bound",
            phase="result_bound",
            candidate_count=len(matched),
            bound=maximum_chars,
            catalog_fingerprint=fingerprint,
        )
    if _provider_chars(response) > maximum_chars:
        raise CapabilityCatalogUnavailable(
            "Capability catalog page metadata cannot fit the provider result bound",
            phase="result_bound",
            candidate_count=len(matched),
            bound=maximum_chars,
            catalog_fingerprint=fingerprint,
        )
    return response


def get_capability_detail(
    *,
    db: Session,
    context: CapabilityCatalogContext,
    kind: str,
    resource_id: str,
    catalog_fingerprint: str,
    detail_hash: str | None = None,
    start: int | None = None,
    max_chars: int | None = None,
    extensions: Sequence[CapabilityCatalogExtension] = (),
) -> dict[str, Any]:
    """Reauthorize and return one exact hash-addressed capability detail."""

    records = build_authorized_capability_catalog(
        db=db,
        context=context,
        extensions=extensions,
    )
    current_fingerprint = capability_catalog_fingerprint(records)
    if catalog_fingerprint != current_fingerprint:
        raise CapabilityCatalogRequestError(
            "The authorized capability catalog changed. Search again before selecting a resource."
        )
    record = next(
        (
            item
            for item in records
            if item.kind == str(kind).strip()
            and item.resource_id == str(resource_id).strip()
        ),
        None,
    )
    if record is None:
        raise CapabilityCatalogRequestError(
            "Capability is unavailable to the current authenticated request."
        )
    serialized = _canonical_json(record.exact_detail())
    current_detail_hash = _sha256(serialized)
    common = {
        "success": True,
        "kind": record.kind,
        "resource_id": record.resource_id,
        "catalog_fingerprint": current_fingerprint,
        "detail_hash": current_detail_hash,
        "detail_length": len(serialized),
        "authorization": "reauthorized",
    }
    requested_cap = normalize_page_limit(
        max_chars,
        default=get_agent_studio_provider_tool_result_inline_max_chars(),
        maximum=get_agent_studio_provider_tool_result_inline_max_chars(),
    )
    if start is None:
        return {
            **common,
            "view": "summary",
            "next_call": {
                "tool": "get_studio_capability_detail",
                "arguments": {
                    "kind": record.kind,
                    "resource_id": record.resource_id,
                    "catalog_fingerprint": current_fingerprint,
                    "detail_hash": current_detail_hash,
                    "start": 0,
                    "max_chars": requested_cap,
                },
            },
        }
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise CapabilityCatalogRequestError("start must be a non-negative integer")
    if start > len(serialized):
        raise CapabilityCatalogRequestError("start exceeds capability detail length")
    if detail_hash != current_detail_hash:
        raise CapabilityCatalogRequestError(
            "Capability detail changed. Restart from the current detail summary."
        )

    def chunk(end: int) -> dict[str, Any]:
        complete = end == len(serialized)
        return {
            **common,
            "view": "chunk",
            "returned_range": {"start": start, "end": end},
            "content": serialized[start:end],
            "complete": complete,
            "next_call": None
            if complete
            else {
                "tool": "get_studio_capability_detail",
                "arguments": {
                    "kind": record.kind,
                    "resource_id": record.resource_id,
                    "catalog_fingerprint": current_fingerprint,
                    "detail_hash": current_detail_hash,
                    "start": end,
                    "max_chars": requested_cap,
                },
            },
        }

    requested_end = min(start + requested_cap, len(serialized))
    result = chunk(requested_end)
    provider_cap = get_agent_studio_provider_tool_result_inline_max_chars()
    if _provider_chars(result) <= provider_cap:
        return result
    low = start + 1
    high = requested_end - 1
    fitting: dict[str, Any] | None = None
    while low <= high:
        candidate_end = (low + high) // 2
        candidate = chunk(candidate_end)
        if _provider_chars(candidate) <= provider_cap:
            fitting = candidate
            low = candidate_end + 1
        else:
            high = candidate_end - 1
    if fitting is None:
        raise CapabilityCatalogUnavailable(
            "Capability detail metadata cannot fit the provider result bound",
            phase="detail_bound",
            candidate_count=1,
            bound=provider_cap,
            catalog_fingerprint=current_fingerprint,
        )
    return fitting


__all__ = [
    "CAPABILITY_KINDS",
    "CapabilityCatalogContext",
    "CapabilityCatalogExtension",
    "CapabilityCatalogRequestError",
    "CapabilityCatalogUnavailable",
    "CapabilityRecord",
    "build_authorized_capability_catalog",
    "capability_catalog_fingerprint",
    "get_capability_detail",
    "search_capabilities",
]
