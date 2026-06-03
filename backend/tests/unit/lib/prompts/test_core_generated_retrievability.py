"""Everything Task 2 removed from the prompt must still be fetchable on demand.

Task 2 slimmed the ``core_generated`` prompt layer, dropping the inlined tool
inventory, the envelope object dump (with per-field required flags), the
per-field validator-binding map (with selectors/input_fields), and the
per-binding CURIE/ontology allow-lists. The design promise is that none of
that detail was lost: it all remains retrievable on demand through
``get_agent_contract``. These guard tests prove that promise so a future
change that breaks ``get_agent_contract`` coverage gets caught.

Note on shape: for the ``domain_envelope``, ``validator_bindings``, and
``ontology_constraints`` topics, ``get_agent_contract`` returns the detail
nested inside a ``domain_packs`` list (one entry per resolved pack), not at the
top level. These assertions therefore reach into ``domain_packs`` to confirm
the data is genuinely present (non-vacuous), not just that a key exists.
"""

from src.lib.agent_contracts import get_agent_contract

# Canonical system extractor agent id; resolves in the production AGENT_REGISTRY
# (verified live) and is the id used in the test_assembly fixtures.
AGENT = "phenotype_extractor"


def test_tool_inventory_still_available():
    """The inlined tool inventory is gone from the prompt; it must remain fetchable."""
    result = get_agent_contract(agent_id=AGENT, topic="tools")
    assert result.get("success") is True
    tools = result.get("tools")
    assert tools, "tool inventory must remain retrievable"
    # Each tool carries the descriptive fields the prompt used to inline.
    first = tools[0]
    assert first.get("tool_id")
    assert "description" in first


def test_validator_bindings_with_selectors_available_at_detail():
    """Per-binding selectors/input_fields (dropped from the prompt) must be fetchable."""
    result = get_agent_contract(
        agent_id=AGENT, topic="validator_bindings", detail_level="detail"
    )
    assert result.get("success") is True
    domain_packs = result.get("domain_packs")
    assert domain_packs, "validator bindings must resolve at least one domain pack"

    # Find a pack that actually carries bindings, then confirm a binding exposes
    # the selector detail (object_types / field_paths / input_fields) the prompt
    # no longer inlines.
    bindings = [b for pack in domain_packs for b in (pack.get("bindings") or [])]
    assert bindings, "validator bindings must remain retrievable"
    binding = bindings[0]
    assert binding.get("validator_agent"), "binding must name its validator agent"
    # The removed prompt detail was the per-binding selectors / input mapping.
    assert (
        "input_fields" in binding
        or binding.get("field_paths")
        or binding.get("object_types")
    ), "binding selectors/input_fields must remain retrievable at detail level"

    # detail_level=detail also surfaces the richer validator/field-policy maps.
    assert any(
        pack.get("validators") or pack.get("field_policies") for pack in domain_packs
    ), "validator/field-policy detail must be retrievable at detail level"


def test_envelope_required_flags_available_at_detail():
    """Envelope object fields with required flags (dropped from prompt) must be fetchable."""
    result = get_agent_contract(
        agent_id=AGENT, topic="domain_envelope", detail_level="detail"
    )
    assert result.get("success") is True
    domain_packs = result.get("domain_packs")
    assert domain_packs, "domain envelope must resolve at least one domain pack"

    pack = domain_packs[0]
    assert pack.get("domain_pack_id")
    object_definitions = pack.get("object_definitions")
    assert object_definitions, "envelope object definitions must remain retrievable"

    # The dropped prompt detail was the per-field required flags. At detail level
    # each object definition carries its fields, each with a ``required`` flag.
    fields = [
        field
        for object_definition in object_definitions
        for field in (object_definition.get("fields") or [])
    ]
    assert fields, "envelope object fields must remain retrievable at detail level"
    assert all(
        "required" in field for field in fields
    ), "per-field required flags must remain retrievable at detail level"


def test_ontology_accepted_terms_available():
    """Accepted ontology terms/constraints (the slim pointer now directs here) must be fetchable."""
    result = get_agent_contract(agent_id=AGENT, topic="ontology_constraints")
    assert result.get("success") is True
    domain_packs = result.get("domain_packs")
    assert domain_packs, "ontology constraints must resolve at least one domain pack"

    constrained_fields = [
        field
        for pack in domain_packs
        for field in (pack.get("constrained_fields") or [])
    ]
    assert constrained_fields, "ontology-constrained fields must remain retrievable"

    # Each constrained field exposes the constraint detail (field_type plus the
    # enum/model/object-type refs and validation policy that encode accepted
    # terms) that the prompt no longer inlines.
    field = constrained_fields[0]
    assert field.get("field_path")
    assert field.get("object_type")
    constraints = field.get("constraints")
    assert constraints, "constraint detail (accepted-term refs) must remain retrievable"
