"""Custom agents look up their own scoped contracts (ALL-1295 / KANBAN-1852).

``get_agent_contract`` resolved only packaged agents, so a curator's custom
agent (``ca_*``) asking for its own contract got "Agent ... was not found." and
could not see its saved tools, profile fields or validator bindings. The
contract now resolves the running agent's pinned revision, and any other custom
agent under the existing custom-agent visibility rules, with the same scoping,
paging and budgets as packaged agents.
"""

from __future__ import annotations

import ast
import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import Agent, RunConfig, Runner
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from src.lib import agent_contracts
from src.lib.agent_contracts import AGENT_CONTRACT_TOPICS, get_agent_contract
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.agent_execution_revision import AgentExecutionReceipt, AgentExecutionSnapshot
from src.schemas.domain_validator import DomainValidatorResultBase

from .domain_packs.test_profile_validation import example, resolve  # noqa: F401
from .test_agent_contract_scoping import (  # noqa: F401
    STAGE_FIELD,
    VALIDATOR_AGENT,
    _agent_registry,
    _chars,
    _default_budget,
    _tool_details,
    _wide_registries,
    _wide_schema,
)

VALIDATOR_SCHEMA = "FixtureValidatorResult"
PROFILE_TOOLS = ["get_agent_contract", "tool_00", "tool_01"]


class FixtureValidatorResult(DomainValidatorResultBase):
    """Packaged validator result schema a custom validator keeps."""


def _schema(name: str):
    return FixtureValidatorResult if name == VALIDATOR_SCHEMA else _wide_schema()


def _registry() -> dict[str, dict]:
    registry = _agent_registry()
    registry[VALIDATOR_AGENT] = {**registry[VALIDATOR_AGENT], "output_schema": VALIDATOR_SCHEMA}
    return registry


@pytest.fixture
def reported(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        agent_contracts,
        "report_payload_contract_violation",
        lambda violation, **kwargs: calls.append((violation, kwargs)) or True,
    )
    return calls


class _Db:
    """Primary-key reads only; the running agent's pin is read by identity."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, object], object] = {}

    def get(self, model, key):
        return self.rows.get((model.__name__, key))


def _snapshot(
    *,
    output_contract,
    tool_ids,
    curation=None,
    template_source=None,
    group_tool_policy=None,
) -> AgentExecutionSnapshot:
    from src.lib.prompts.assembly import _bundle, _make_layer

    instructions = "Curator instructions"
    bundle = _bundle(
        "ca_fixture",
        [
            _make_layer(
                layer_id="ca_fixture:base_prompt",
                kind="base_prompt",
                title="Custom agent main prompt",
                content=instructions,
                provenance="custom_agent",
                editable=True,
                locked=False,
                source_ref="custom_agent:test",
            )
        ],
    )
    return AgentExecutionSnapshot.model_validate(
        {
            "model_id": "test-model",
            "model_temperature": 0.0,
            "model_reasoning": None,
            "instructions": instructions,
            "instructions_hash": "sha256:" + hashlib.sha256(instructions.encode()).hexdigest(),
            "prompt_layer_manifest": bundle.to_manifest(),
            "group_prompt_layers": {},
            "tool_ids": list(tool_ids),
            "system_managed_tool_ids": [],
            "group_tool_policy": group_tool_policy or {"rules": []},
            "allowed_group_ids": [],
            "inherited_allowed_group_ids": [],
            "group_rules_enabled": False,
            "group_rules_component": None,
            "group_prompt_overrides": {},
            "template_source": template_source,
            "output_contract": output_contract,
            "curation": curation,
            "structured_finalization": None,
        }
    )


def _save(db: _Db, saved: AgentExecutionSnapshot, *, name: str) -> AgentExecutionReceipt:
    agent_uuid = uuid4()
    agent_key = f"ca_{agent_uuid}"
    row = SimpleNamespace(
        id=uuid4(),
        agent_id=agent_uuid,
        revision=3,
        fingerprint=saved.fingerprint(),
        snapshot=saved.model_dump(mode="json"),
    )
    db.rows[("AgentExecutionRevision", row.id)] = row
    db.rows[("Agent", agent_uuid)] = SimpleNamespace(id=agent_uuid, agent_key=agent_key, name=name)
    return AgentExecutionReceipt(
        agent_id=agent_uuid,
        agent_key=agent_key,
        agent_revision_id=row.id,
        revision=row.revision,
        fingerprint=row.fingerprint,
        output_contract=saved.output_contract,
    )


def _caller(receipt: AgentExecutionReceipt, groups=("FB",)):
    """The runtime agent object the SDK hands to the tool (ToolContext.agent)."""

    return SimpleNamespace(
        agent_key=receipt.agent_key,
        execution_receipt=receipt.model_dump(mode="json"),
        authenticated_groups=tuple(groups),
    )


@pytest.fixture
def custom(example, monkeypatch):  # noqa: F811 - pytest injects the imported fixture
    raw, capability, pack = example
    profile_receipt, profile = resolve(raw)
    registries = {
        **_wide_registries(),
        "generic": DomainPackValidationRegistry.from_domain_pack(pack),
    }
    db = _Db()
    profile_agent = _save(
        db,
        _snapshot(
            output_contract=profile_receipt.output_contract,
            tool_ids=PROFILE_TOOLS,
            curation={"adapter_key": "generic", "domain_pack_id": "generic", "launchable": True},
            template_source="fixture_expression_extractor",
        ),
        name="Curator record extractor",
    )
    validator_agent = _save(
        db,
        _snapshot(
            output_contract={
                "output_state": "structured_extraction",
                "output_mode": "domain",
                "output_schema_key": VALIDATOR_SCHEMA,
            },
            tool_ids=["get_agent_contract", "tool_02"],
            template_source=VALIDATOR_AGENT,
        ),
        name="Curator stage lookup",
    )
    none_agent = _save(
        db,
        _snapshot(output_contract={"output_state": "none"}, tool_ids=["get_agent_contract"]),
        name="Curator helper",
    )
    monkeypatch.setattr(agent_contracts, "_custom_agent_session", lambda: nullcontext(db), raising=False)
    monkeypatch.setattr(
        "src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
        lambda _db, receipt: profile,
    )
    catalog_calls: list = []
    monkeypatch.setattr(
        "src.lib.domain_packs.profile_validation.capability_catalog",
        lambda **kwargs: catalog_calls.append(kwargs) or [capability],
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.custom_profile_validators.runtime_validator_user_id",
        lambda identity=None: None,
    )
    return SimpleNamespace(
        db=db,
        registries=registries,
        profile=profile,
        profile_agent=profile_agent,
        validator_agent=validator_agent,
        none_agent=none_agent,
        catalog_calls=catalog_calls,
    )


def _call(custom, **kwargs):
    kwargs.setdefault("agent_registry", _registry())
    kwargs.setdefault("registries", custom.registries)
    kwargs.setdefault("tool_details_resolver", _tool_details)
    kwargs.setdefault("output_schema_resolver", _schema)
    return get_agent_contract(**kwargs)


def _own(custom, receipt, **kwargs):
    return _call(custom, agent_id=receipt.agent_key, caller=_caller(receipt), **kwargs)


def _all_pages(custom, receipt, **kwargs):
    responses, items, cursor = [], [], None
    while True:
        response = _own(custom, receipt, cursor=cursor, **kwargs)
        assert response["success"] is True, response
        responses.append(response)
        items.extend(response["items"])
        cursor = response["page"]["next_cursor"]
        if cursor is None:
            return responses, items


# ---------------------------------------------------------------------------
# Regression: the running custom agent reads its own contract through the tool.
# ---------------------------------------------------------------------------


class ScriptedCustomExtractor(Model):
    """Mocked custom agent: read its own validator bindings, then answer."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.calls = 0
        self.tool_outputs: list[str] = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.calls += 1
        self.tool_outputs = [
            item["output"]
            for item in input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if self.calls == 1:
            arguments = {
                "agent_id": self.agent_id,
                "topic": "validator_bindings",
                "detail_level": "detail",
            }
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        type="function_call",
                        name="get_agent_contract",
                        call_id="contract-1",
                        arguments=json.dumps(arguments),
                    )
                ],
                usage=Usage(requests=1),
                response_id="response-1",
            )
        contract = ast.literal_eval(self.tool_outputs[-1])
        answer = {
            "success": contract["success"],
            "error": contract.get("error"),
            "bindings": [
                item["validator_binding_id"]
                for item in contract.get("items", [])
                if item["kind"] == "validator_binding"
            ],
        }
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="final",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(type="output_text", text=json.dumps(answer), annotations=[])
                    ],
                )
            ],
            usage=Usage(requests=1),
            response_id="response-final",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Non-streaming agent expected")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_running_custom_agent_reads_its_own_contract_through_the_tool(
    custom, monkeypatch, reported
):
    from src.lib.domain_packs.profile_validation import profile_mapping_binding_id
    from src.lib.openai_agents.tools.agent_contract import get_agent_contract as contract_tool

    receipt = custom.profile_agent
    monkeypatch.setattr(agent_contracts, "domain_pack_validation_registries", lambda: custom.registries)
    monkeypatch.setattr(agent_contracts, "_default_agent_registry", _registry)
    model = ScriptedCustomExtractor(receipt.agent_key)
    agent = Agent(name="Curator record extractor", model=model, tools=[contract_tool])
    # Attributes the catalog attaches to a pinned custom agent at build time.
    agent.agent_key = receipt.agent_key
    agent.execution_receipt = receipt.model_dump(mode="json")
    agent.authenticated_groups = ("FB",)

    result = await Runner.run(agent, "extract records", run_config=RunConfig(tracing_disabled=True))

    answer = json.loads(result.final_output)
    assert answer["error"] is None
    assert answer["success"] is True
    mapping = custom.profile.contract.validator_mappings[0]
    assert answer["bindings"] == [profile_mapping_binding_id(custom.profile, mapping)]
    assert all(len(output) <= _default_budget() for output in model.tool_outputs)
    # The saved profile overlay is the whole scope: no packaged pack leaks in.
    assert "fixture.expression" not in model.tool_outputs[-1]
    assert reported == []


# ---------------------------------------------------------------------------
# Every topic for a profile-bound custom extractor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic", sorted(AGENT_CONTRACT_TOPICS - {"field"}))
@pytest.mark.parametrize("detail_level", ["summary", "detail"])
def test_profile_custom_agent_pages_every_topic_within_budget(custom, topic, detail_level, reported):
    responses, items = _all_pages(
        custom, custom.profile_agent, topic=topic, detail_level=detail_level
    )

    for response in responses:
        assert _chars(response) <= _default_budget()
        identity = response["custom_agent"]
        assert identity["agent_id"] == custom.profile_agent.agent_key
        assert identity["revision_source"] == "running_agent"
        assert identity["execution_revision"] == 3
        assert identity["parent_agent_id"] == "fixture_expression_extractor"
        assert identity["output_mode"] == "profile_bound_generic"
        assert identity["generic_profile_ref"] == custom.profile.receipt
    assert len(items) == responses[0]["page"]["total_count"]
    packs = {item.get("domain_pack_id") for item in items} - {None}
    assert packs <= {"generic"}
    assert reported == []


def test_profile_custom_agent_tools_are_its_saved_selection(custom):
    result = _own(custom, custom.profile_agent, topic="tools")

    assert [item["tool_id"] for item in result["items"]] == PROFILE_TOOLS


def test_profile_custom_agent_output_schema_points_to_profile_fields(custom):
    listing = _own(custom, custom.profile_agent, topic="output_schema")
    by_field = _own(custom, custom.profile_agent, topic="output_schema", field_path="paper_name")

    assert listing["success"] is True
    assert listing["output_schema"] is None
    assert listing["items"] == []
    assert "topic=domain_envelope" in listing["note"]
    assert by_field["success"] is False
    assert "topic=domain_envelope" in by_field["hint"]


def test_profile_custom_agent_exposes_saved_profile_fields(custom):
    envelope = _own(custom, custom.profile_agent, topic="domain_envelope")
    field = _own(
        custom,
        custom.profile_agent,
        topic="field",
        field_path="attributes.resolved_id",
        detail_level="detail",
    )

    assert [pack["domain_pack_id"] for pack in envelope["domain_packs"]] == ["generic"]
    assert [pack["relation"] for pack in envelope["domain_packs"]] == ["owned"]
    obj, = envelope["items"]
    assert obj["object_type"] == "generic_object"
    assert obj["display_name"] == "Records"
    assert {"attributes.paper_name", "attributes.resolved_id"} <= set(obj["field_paths"])
    item, = field["items"]
    assert item["ref"] == "field|generic|generic_object|attributes.resolved_id"
    assert item["field"]["field_path"] == "attributes.resolved_id"
    assert [binding["validator_agent"] for binding in item["validator_bindings"]] == [
        {"package_id": "example", "agent_id": "lookup"}
    ]


def test_profile_custom_agent_validator_bindings_carry_the_saved_mapping(custom):
    from src.lib.domain_packs.profile_validation import profile_mapping_binding_id

    mapping = custom.profile.contract.validator_mappings[0]
    summary = _own(custom, custom.profile_agent, topic="validator_bindings")
    detail = _own(
        custom,
        custom.profile_agent,
        topic="validator_bindings",
        field_path="attributes.paper_name",
        detail_level="detail",
    )

    binding, = summary["items"]
    assert binding["validator_binding_id"] == profile_mapping_binding_id(custom.profile, mapping)
    assert binding["validator_agent"] == {"package_id": "example", "agent_id": "lookup"}
    detailed = next(item for item in detail["items"] if item["kind"] == "validator_binding")
    assert detailed["profile_validation"]["mapping"] == mapping.model_dump(mode="json")
    assert all(item["domain_pack_id"] == "generic" for item in detail["items"])
    # The profile is compiled for the running agent's authenticated groups.
    assert custom.catalog_calls[-1]["active_group_ids"] == ("FB",)


def test_unavailable_profile_mapping_is_explicit_not_dropped(custom, monkeypatch):
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [])
    mapping = custom.profile.contract.validator_mappings[0]

    listing = _own(custom, custom.profile_agent, topic="validator_bindings", detail_level="detail")
    scoped = _own(
        custom, custom.profile_agent, topic="validator_bindings", field_path="attributes.resolved_id"
    )
    unrelated = _own(
        custom, custom.profile_agent, topic="validator_bindings", field_path="semantic_class"
    )

    assert listing["custom_agent"]["unavailable_profile_mapping_count"] == 1
    item, = listing["items"]
    assert item["kind"] == "profile_validator_mapping"
    assert item["available"] is False
    assert item["unavailable_reasons"]
    assert item["mapping_id"] == mapping.mapping_id
    assert item["profile_validator_mapping"] == mapping.model_dump(mode="json")
    assert [entry["mapping_id"] for entry in scoped["items"]] == [mapping.mapping_id]
    assert "profile_validator_mapping" not in scoped["items"][0]
    assert unrelated["items"] == []


def test_profile_custom_agent_unknown_field_never_falls_back(custom, reported):
    result = _own(custom, custom.profile_agent, topic="field", field_path="attributes.paper")

    assert result["success"] is False
    assert "was not found" in result["error"]
    assert "attributes.paper_name" in result["suggested_field_paths"]
    assert result["searched_domain_pack_ids"] == ["generic"]
    assert "items" not in result
    assert reported == []


# ---------------------------------------------------------------------------
# A custom validator serves its packaged parent's bindings
# ---------------------------------------------------------------------------


def test_custom_validator_contract_uses_parent_bindings_and_its_own_schema(custom, reported):
    receipt = custom.validator_agent
    bindings = _own(
        custom,
        receipt,
        topic="validator_bindings",
        field_path=STAGE_FIELD,
        detail_level="detail",
    )
    schema = _own(custom, receipt, topic="output_schema", field_path="validator_binding_id")
    unrelated = _own(custom, receipt, topic="validator_bindings", domain_pack_id="fixture.unrelated")

    assert bindings["custom_agent"]["validates_for_agent_id"] == VALIDATOR_AGENT
    assert [pack["domain_pack_id"] for pack in bindings["domain_packs"]] == [
        "fixture.expression",
        "fixture.phenotype",
    ]
    assert [pack["relation"] for pack in bindings["domain_packs"]] == ["validator", "validator"]
    targeted = [item for item in bindings["items"] if item["kind"] == "validator_binding"]
    assert targeted and all(item["targeted_to_agent"] for item in targeted)
    assert all(item["field_paths"] == [STAGE_FIELD] for item in targeted)
    assert _chars(bindings) <= _default_budget()
    assert schema["output_schema"] == VALIDATOR_SCHEMA
    assert schema["items"][0]["field_path"] == "validator_binding_id"
    assert unrelated["success"] is False and "is not in scope" in unrelated["error"]
    assert reported == []


def test_custom_agent_with_changed_output_does_not_inherit_parent_bindings(custom):
    db = custom.db
    receipt = _save(
        db,
        _snapshot(
            output_contract={
                "output_state": "structured_extraction",
                "output_mode": "domain",
                "output_schema_key": "WideEnvelope",
            },
            tool_ids=["get_agent_contract"],
            template_source=VALIDATOR_AGENT,
        ),
        name="Reworked output",
    )

    result = _own(custom, receipt, topic="validator_bindings")

    assert result["success"] is True
    assert result["custom_agent"]["validates_for_agent_id"] is None
    assert result["items"] == []
    assert result["domain_packs"] == []


def test_custom_agent_without_structured_output_reports_it(custom):
    tools = _own(custom, custom.none_agent, topic="tools")
    schema = _own(custom, custom.none_agent, topic="output_schema")
    field = _own(custom, custom.none_agent, topic="field", field_path=STAGE_FIELD)

    assert [item["tool_id"] for item in tools["items"]] == ["get_agent_contract"]
    assert schema["output_schema"] is None
    assert "no structured output" in schema["note"]
    assert field["success"] is False
    assert "has no domain packs in scope" in field["error"]


def test_group_tool_policy_is_resolved_for_the_running_agent(custom):
    receipt = _save(
        custom.db,
        _snapshot(
            output_contract={"output_state": "none"},
            tool_ids=["get_agent_contract"],
            group_tool_policy={
                "rules": [
                    {"tool_id": "tool_05", "allowed_group_ids": ["FB"], "field_paths": ["gene.symbol"]}
                ]
            },
        ),
        name="Group tools",
    )

    team_a = _call(custom, agent_id=receipt.agent_key, caller=_caller(receipt), topic="tools")
    team_b = _call(
        custom, agent_id=receipt.agent_key, caller=_caller(receipt, groups=("WB",)), topic="tools"
    )

    assert [item["tool_id"] for item in team_a["items"]] == ["get_agent_contract", "tool_05"]
    assert [item["tool_id"] for item in team_b["items"]] == ["get_agent_contract"]


def test_cursor_is_bound_to_the_custom_revision(custom):
    first = _own(custom, custom.profile_agent, topic="tools", limit=1)
    cursor = first["page"]["next_cursor"]
    assert _own(custom, custom.profile_agent, topic="tools", limit=1, cursor=cursor)["success"]

    other = _save(
        custom.db,
        _snapshot(
            output_contract=custom.profile_agent.output_contract,
            tool_ids=PROFILE_TOOLS,
            curation={"adapter_key": "generic", "domain_pack_id": "generic", "launchable": True},
            template_source="fixture_expression_extractor",
        ),
        name="Same tools, other agent",
    )
    stale = _own(custom, other, topic="tools", limit=1, cursor=cursor)

    assert stale["success"] is False
    assert "belongs to a different request" in stale["error"]


# ---------------------------------------------------------------------------
# Visibility: other custom agents follow the custom-agent access rules
# ---------------------------------------------------------------------------


@pytest.fixture
def visibility(custom, monkeypatch):
    from src.lib.agent_studio.execution_revision_service import ExecutionRevisionNotFoundError

    calls: list = []
    visible = {custom.validator_agent.agent_key: custom.validator_agent}

    def current(db, agent_key, user_id, *, active_group_ids):
        assert db is custom.db
        calls.append(("head", agent_key, user_id, list(active_group_ids)))
        if agent_key not in visible:
            raise ExecutionRevisionNotFoundError("Executable agent revision not found")
        return visible[agent_key]

    def revision(db, agent_id, revision_id, user_id, *, active_group_ids):
        calls.append(("revision", agent_id, user_id, list(active_group_ids)))
        row = db.get(SimpleNamespace(__name__="AgentExecutionRevision"), revision_id)
        return row, AgentExecutionSnapshot.model_validate(row.snapshot)

    monkeypatch.setattr(
        "src.lib.agent_studio.execution_revision_service.current_execution_receipt", current
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.execution_revision_service.get_execution_revision", revision
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.custom_profile_validators.runtime_validator_user_id",
        lambda identity=None: 7,
    )
    return calls


def test_visible_custom_agent_is_read_from_its_authorized_saved_head(custom, visibility):
    caller = _caller(custom.profile_agent)
    result = _call(
        custom,
        agent_id=custom.validator_agent.agent_key,
        caller=caller,
        topic="validator_bindings",
        field_path=STAGE_FIELD,
    )

    assert result["success"] is True, result
    assert result["custom_agent"]["revision_source"] == "saved_head"
    assert result["custom_agent"]["name"] == "Curator stage lookup"
    assert visibility == [
        ("head", custom.validator_agent.agent_key, 7, ["FB"]),
        ("revision", custom.validator_agent.agent_id, 7, ["FB"]),
    ]


def test_other_curators_private_custom_agent_is_not_visible(custom, visibility, reported):
    hidden = custom.none_agent
    result = _call(
        custom,
        agent_id=hidden.agent_key,
        caller=_caller(custom.profile_agent),
        topic="tools",
    )

    assert result["success"] is False
    assert result["error"] == f"Agent {hidden.agent_key} was not found."
    assert "items" not in result and "custom_agent" not in result
    assert "Curator helper" not in json.dumps(result)
    assert visibility == [("head", hidden.agent_key, 7, ["FB"])]
    assert reported == []


def test_running_agent_receipt_grants_only_its_own_contract(custom, visibility):
    # The caller's pin is for the profile agent; asking for another agent id
    # with that caller goes through normal visibility, not the running pin.
    spoofed = SimpleNamespace(
        agent_key=custom.none_agent.agent_key,
        execution_receipt=custom.profile_agent.model_dump(mode="json"),
        authenticated_groups=("FB",),
    )

    result = _call(custom, agent_id=custom.none_agent.agent_key, caller=spoofed, topic="tools")

    assert result["success"] is False
    assert "does not match" in result["error"]
    assert visibility == []


def test_custom_agent_lookup_without_authenticated_user_is_not_visible(custom, monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.execution_revision_service.current_execution_receipt",
        lambda *args, **kwargs: pytest.fail("visibility was not enforced before reading the head"),
    )

    result = _call(custom, agent_id=custom.validator_agent.agent_key, topic="tools")

    assert result["success"] is False
    assert result["error"] == f"Agent {custom.validator_agent.agent_key} was not found."


def test_own_contract_needs_no_user_lookup_but_must_match_its_pin(custom):
    receipt = custom.profile_agent
    tampered = receipt.model_copy(update={"fingerprint": "sha256:" + "f" * 64})

    own = _own(custom, receipt, topic="tools")
    mismatch = _call(custom, agent_id=receipt.agent_key, caller=_caller(tampered), topic="tools")

    assert own["success"] is True
    assert mismatch["success"] is False
    assert "does not match" in mismatch["error"]
    assert "items" not in mismatch


def test_malformed_custom_agent_id_is_not_found(custom):
    result = _call(custom, agent_id="ca_not-a-uuid", topic="tools")

    assert result["success"] is False
    assert result["error"] == "Agent ca_not-a-uuid was not found."


def test_custom_agent_runtime_note_names_the_agent_id():
    from src.lib.agent_contracts import custom_agent_contract_runtime_note

    note = custom_agent_contract_runtime_note("ca_1234")

    assert "agent_id=ca_1234" in note
    assert "get_agent_contract" in note
