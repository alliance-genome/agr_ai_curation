"""Everything Task 2 removed from the prompt must still be fetchable on demand.

Task 2 slimmed the ``core_generated`` prompt layer, dropping the inlined tool
inventory, the envelope object dump (with per-field required flags), the
per-field validator-binding map (with selectors/input_fields), and the
per-binding CURIE/ontology allow-lists. The design promise is that none of
that detail was lost: it all remains retrievable on demand through
``get_agent_contract``. These guard tests prove that promise so a future
change that breaks ``get_agent_contract`` coverage gets caught.

Note on shape: every topic returns one bounded page of ``items`` (each with a
``ref`` and ``kind``) plus a ``page`` envelope; ``domain_packs`` lists only the
pack headers in scope. These assertions page through ``items`` to confirm the
data is genuinely present (non-vacuous), not just that a key exists.
"""

from src.lib.agent_contracts import get_agent_contract


def _all_items(**kwargs) -> list[dict]:
    """Follow page.next_cursor until the scoped collection is exhausted."""
    items: list[dict] = []
    cursor = None
    while True:
        result = get_agent_contract(cursor=cursor, **kwargs)
        assert result.get("success") is True, result
        items.extend(result["items"])
        cursor = result["page"]["next_cursor"]
        if cursor is None:
            return items

# Canonical system extractor agent id; resolves in the production AGENT_REGISTRY
# (verified live) and is the id used in the test_assembly fixtures.
AGENT = "phenotype_extractor"


def test_tool_inventory_still_available():
    """The inlined tool inventory is gone from the prompt; it must remain fetchable."""
    tools = _all_items(agent_id=AGENT, topic="tools")
    assert tools, "tool inventory must remain retrievable"
    # Each tool carries the descriptive fields the prompt used to inline.
    first = tools[0]
    assert first.get("tool_id")
    assert "description" in first


def test_validator_bindings_with_selectors_available_at_detail():
    """Per-binding selectors/input_fields (dropped from the prompt) must be fetchable."""
    items = _all_items(
        agent_id=AGENT, topic="validator_bindings", detail_level="detail"
    )

    # Confirm a binding exposes the selector detail (object_types / field_paths /
    # input_fields) the prompt no longer inlines.
    bindings = [item for item in items if item["kind"] == "validator_binding"]
    assert bindings, "validator bindings must remain retrievable"
    binding = bindings[0]
    assert binding.get("validator_agent"), "binding must name its validator agent"
    # The removed prompt detail was the per-binding selectors / input mapping.
    assert (
        "input_fields" in binding
        or binding.get("field_paths")
        or binding.get("object_types")
    ), "binding selectors/input_fields must remain retrievable at detail level"

    # detail_level=detail also surfaces validator entries and attachment options;
    # field policies are returned for a selected field.
    assert any(item["kind"] == "validator" for item in items) or any(
        binding.get("validation_attachments") for binding in bindings
    ), "validator detail must be retrievable at detail level"
    field_path = next(path for binding in bindings for path in binding.get("field_paths") or [])
    field_items = _all_items(
        agent_id=AGENT,
        topic="validator_bindings",
        detail_level="detail",
        field_path=field_path,
    )
    assert any(item["kind"] == "field_policy" for item in field_items), (
        "the selected field's policy must be retrievable at detail level"
    )


def test_envelope_required_flags_available_at_detail():
    """Envelope object fields with required flags (dropped from prompt) must be fetchable."""
    result = get_agent_contract(
        agent_id=AGENT, topic="domain_envelope", detail_level="detail"
    )
    assert result.get("success") is True
    domain_packs = result.get("domain_packs")
    assert domain_packs, "domain envelope must resolve at least one domain pack"
    assert domain_packs[0].get("domain_pack_id")

    items = _all_items(agent_id=AGENT, topic="domain_envelope", detail_level="detail")
    object_definitions = [item for item in items if item["kind"] == "object_definition"]
    assert object_definitions, "envelope object definitions must remain retrievable"

    # The dropped prompt detail was the per-field required flags. At detail level
    # each field definition is its own item with a ``required`` flag.
    fields = [item for item in items if item["kind"] == "field_definition"]
    assert fields, "envelope object fields must remain retrievable at detail level"
    assert all(
        "required" in field for field in fields
    ), "per-field required flags must remain retrievable at detail level"


def test_validator_curie_allow_list_still_fetchable():
    """The literal CURIE allow-lists removed from the prompt (e.g. accepted_prefixes)
    must still be reachable via get_agent_contract validator_bindings detail.

    The slimmed prompt used to inline a per-binding ``accepted_prefixes`` literal
    allow-list. This proves that exact literal survives in the contract data, not
    just that some selector key exists. Shape confirmed against live data:
    ``validator_binding_id`` is the binding identifier; ``input_fields`` is a dict
    keyed by field name; ``accepted_prefixes`` is a literal-source selector whose
    ``value`` is the allow-list. The live phenotype allow-list is ["MP",
    "WBPhenotype"] (note: ZP is NOT part of this binding's accepted_prefixes).
    """
    bindings = [
        item
        for item in _all_items(
            agent_id=AGENT, topic="validator_bindings", detail_level="detail"
        )
        if item["kind"] == "validator_binding"
    ]
    assert bindings, "validator bindings must remain retrievable"

    target = next(
        binding
        for binding in bindings
        if binding.get("validator_binding_id") == "phenotype_term_ontology_validator"
    )
    input_fields = target["input_fields"]
    assert "accepted_prefixes" in input_fields, (
        "the literal accepted_prefixes allow-list must remain fetchable at detail level"
    )
    accepted = input_fields["accepted_prefixes"]
    assert accepted.get("source") == "literal"
    values = accepted["value"]
    assert "MP" in values and "WBPhenotype" in values, (
        f"the literal CURIE allow-list must survive; got {values!r}"
    )


def test_ontology_accepted_terms_available():
    """Accepted ontology terms/constraints (the slim pointer now directs here) must be fetchable."""
    constrained_fields = _all_items(agent_id=AGENT, topic="ontology_constraints")
    assert constrained_fields, "ontology-constrained fields must remain retrievable"

    # Each constrained field exposes the constraint detail (field_type plus the
    # enum/model/object-type refs and validation policy that encode accepted
    # terms) that the prompt no longer inlines.
    field = constrained_fields[0]
    assert field.get("field_path")
    assert field.get("object_type")
    constraints = field.get("constraints")
    assert constraints, "constraint detail (accepted-term refs) must remain retrievable"
