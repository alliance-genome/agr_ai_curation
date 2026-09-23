"""One pinned profile contract for tool schemas and non-coercing record checks.

Authorization belongs to the revision resolver. This pure service consumes its
verified pin and normalized contract; it never looks up a mutable profile head.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS, LOOKUP_OUTCOME_KEY, LOOKUP_OUTCOMES, MENTION_KEY, RESOLUTION_STATE_KEY,
    RESOLUTION_STATES, VALIDATOR_CURATOR_MESSAGE_KEY, VALIDATOR_EXPLANATION_KEY,
    OVERRULED_KEY_PREFIX, ResolvableSpec, ResolvableValueError, apply_curator_identity,
    check_resolvable_value, has_resolution_state, overruled_key, unresolved_value,
)
from src.lib.openai_agents.config import (
    get_generic_profile_max_issues,
    get_generic_profile_max_record_bytes,
    get_generic_profile_max_record_values,
)
from src.schemas.agent_execution_revision import GenericProfilePin
from src.schemas.generic_extraction_profile import (
    ArrayValueSchema, GenericProfileContract, ObjectValueSchema, ProfileField, ValueSchema,
    canonical_json,
)


# Resolution keys a validator writes into a resolvable value; never the extractor.
RESOLUTION_KEYS = tuple(key for key in CONTRACT_KEYS if key != MENTION_KEY)
_VOCABULARIES = {RESOLUTION_STATE_KEY: RESOLUTION_STATES, LOOKUP_OUTCOME_KEY: LOOKUP_OUTCOMES}


def declared_value_path(path: str) -> str:
    """A concrete attributes path with its array indexes as ``[]`` (e.g. ``attributes.genes[]``)."""
    return re.sub(r"\[[0-9]+\]", "[]", path)


def _mapped_paths(contract: GenericProfileContract) -> tuple[dict[str, list[str]], set[str]]:
    """({destination: [output slot names]}, {field paths mappings read}), in declared ``[]`` notation."""
    destinations: dict[str, list[str]] = {}
    reads: set[str] = set()
    for mapping in contract.validator_mappings:
        for slot, destination in mapping.outputs.items():
            destinations.setdefault(destination, []).append(slot)
        reads.update(item.field_path for item in mapping.inputs.values()
                     if item.source == "field" and item.field_path)
    return destinations, reads


def validator_owned_paths(contract: GenericProfileContract) -> set[str]:
    """Mapped destinations the extractor never writes: validation fills them in.

    A destination a mapping also reads stays the extractor's to write, so
    saved profiles keep their validator input.
    """
    destinations, reads = _mapped_paths(contract)
    return {destination for destination in destinations if destination not in reads}


def resolvable_objects(contract: GenericProfileContract) -> dict[str, tuple[str, ...]]:
    """The profile's resolvable values: {declared object path: identity keys}.

    An object is resolvable when it declares a string ``mention`` field (the
    paper wording) and validator mappings write into it without reading what
    they write; the keys they write are its identity (ALL-1283). Other mapped
    destinations stay plain.
    """
    destinations, reads = _mapped_paths(contract)
    objects: dict[str, list[str]] = {}
    shared: set[str] = set()
    for destination in destinations:
        parent, _, key = destination.rpartition(".")
        schema = _declared_schema(contract.fields, parent)
        if not isinstance(schema, ObjectValueSchema):
            continue
        mention = next((field for field in schema.fields if field.key == MENTION_KEY), None)
        if mention is None or mention.value_schema.kind != "string":
            continue
        if destination in reads or key == MENTION_KEY:
            shared.add(parent)
        keys = objects.setdefault(parent, [])
        if key not in keys:
            keys.append(key)
    return {path: tuple(keys) for path, keys in objects.items() if path not in shared}


_IDENTIFIER_SLOTS = frozenset({"curie", "identifier", "id"})
_IDENTIFIER_NAME = re.compile(r"(?:^|_)(?:id|curie|identifier)$")


def resolvable_specs(contract: GenericProfileContract) -> dict[str, ResolvableSpec]:
    """Each resolvable value's display roles: its identifier key and its label key.

    The identifier is the identity key written by an output slot named exactly
    ``curie``, ``identifier`` or ``id``; failing that, the key whose name or
    writing slot names an identifier (``..._id``). The label is the first
    other identity key.
    """
    destinations, _ = _mapped_paths(contract)
    specs: dict[str, ResolvableSpec] = {}
    for path, identity in resolvable_objects(contract).items():
        def slots(key: str) -> list[str]:
            return destinations.get(f"{path}.{key}", [])
        id_key = next((key for key in identity if _IDENTIFIER_SLOTS.intersection(slots(key))), None)
        if id_key is None:
            id_key = next((key for key in identity
                           if any(_IDENTIFIER_NAME.search(name) for name in [key, *slots(key)])), None)
        label_key = next((key for key in identity if key != id_key), None)
        specs[path] = ResolvableSpec(id_key=id_key, label_key=label_key)
    return specs


def _declared_schema(fields: list[ProfileField], path: str) -> ValueSchema | None:
    """The value schema at a declared ``attributes...`` path (``[]`` steps into array items)."""
    if not path.startswith("attributes."):
        return None
    schema: ValueSchema = ObjectValueSchema(kind="object", fields=fields)
    for part in path.split(".")[1:]:
        key = part.removesuffix("[]")
        if not isinstance(schema, ObjectValueSchema):
            return None
        field = next((field for field in schema.fields if field.key == key), None)
        if field is None:
            return None
        schema = field.value_schema
        if part.endswith("[]"):
            if not isinstance(schema, ArrayValueSchema):
                return None
            schema = schema.items
    return schema


class ProfileIdentityError(ValueError):
    """The provided contract does not match the invocation's immutable receipt."""


class ProfileConformanceError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("Record does not conform to its saved output structure")
        self.issues = issues


class EnvelopeIntegrityError(Exception):
    """The envelope's own structure is broken, independently of the curator's profile.

    KANBAN-1773. A sibling of ProfileConformanceError, deliberately not a
    subclass and deliberately not a ValueError: broad handlers on the extraction
    path catch both of those and re-report them as "Record does not conform to
    its saved output structure", which tells a curator to fix an Output
    Structure that was never wrong.

    Raise this only for failures the curator cannot act on. Genuine profile and
    identity violations keep their own types and wording.
    """

    CURATOR_MESSAGE = (
        "The run encountered an internal problem while saving evidence. "
        "Your Output Structure does not need to change."
    )

    def __init__(self, issues: list[dict[str, Any]], *, code: str = "envelope_integrity") -> None:
        super().__init__(self.CURATOR_MESSAGE)
        self.issues = issues
        self.code = code


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    return {str: "string", bool: "boolean", int: "integer", float: "number",
            dict: "object", list: "array"}.get(type(value), "non_json")


class ResolvedGenericProfile:
    """Immutable normalized bytes, shared by schema/stage/patch/materialization."""

    def __init__(self, pin: GenericProfilePin, contract: GenericProfileContract):
        normalized = GenericProfileContract.model_validate(contract.model_dump(mode="json"))
        if normalized.fingerprint() != pin.fingerprint:
            raise ProfileIdentityError("Profile fingerprint does not match the saved receipt")
        self._pin_json = pin.model_dump_json()
        self._contract_json = canonical_json(normalized.model_dump(mode="json"))

    @property
    def receipt(self) -> dict[str, Any]:
        return json.loads(self._pin_json)

    @property
    def contract(self) -> GenericProfileContract:
        # Consumers receive copies, so editing a draft cannot mutate a bound run.
        return GenericProfileContract.model_validate_json(self._contract_json)

    def resolvable_objects(self) -> dict[str, tuple[str, ...]]:
        return resolvable_objects(self.contract)

    def resolvable_specs(self) -> dict[str, ResolvableSpec]:
        return resolvable_specs(self.contract)

    def validator_owned_paths(self) -> set[str]:
        return validator_owned_paths(self.contract)

    def _extractor_shape(self) -> "_ExtractorShape":
        return _ExtractorShape(self.resolvable_objects(), self.validator_owned_paths())

    def resolvable_container(self, destination: str) -> tuple[str, str] | None:
        """(concrete container path, identity key) when a mapped destination sits in a resolvable value."""
        parent, _, key = destination.rpartition(".")
        identity = self.resolvable_objects().get(declared_value_path(parent))
        return (parent, key) if identity is not None and key in identity else None

    def unresolved_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Stage each resolvable value's paper wording as unresolved and not yet validated.

        Values that already carry a resolution state are kept as they are.
        """
        resolvable = self.resolvable_objects()

        def expand(value: Any, path: str) -> Any:
            if isinstance(value, list):
                return [expand(item, f"{path}[]") for item in value]
            if not isinstance(value, dict):
                return value
            expanded = {key: expand(item, f"{path}.{key}") for key, item in value.items()}
            identity = resolvable.get(path)
            if identity is None or has_resolution_state(expanded):
                return expanded
            mention = expanded.pop(MENTION_KEY, None)
            if not isinstance(mention, str) or not mention.strip():
                return value
            return unresolved_value(mention, identity_keys=identity, **expanded)

        return expand(deepcopy(attributes), "attributes")

    def require_receipt(self, receipt: dict[str, Any]) -> None:
        try:
            supplied = GenericProfilePin.model_validate(receipt).model_dump(mode="json")
        except ValueError as exc:
            raise ProfileIdentityError("Missing or invalid profile receipt") from exc
        if supplied != self.receipt:
            raise ProfileIdentityError("Profile receipt does not match the bound revision")

    def attributes_schema(self) -> dict[str, Any]:
        """Canonical fields only; aliases are recognition prose, never properties.

        A resolvable value takes only what the extractor writes: its paper
        wording and its other declared fields, never its identity or state.
        """
        return _object_schema(self.contract.fields, "attributes", self._extractor_shape())

    def patch_schema(self) -> dict[str, Any]:
        """Typed canonical paths, including array index and subtree replacements."""
        variants = []
        shape = self._extractor_shape()

        def add(path_pattern: str, declared: str, schema: ValueSchema, nullable: bool = False) -> None:
            value = _value_schema(schema, declared, shape)
            if nullable:
                value = {"anyOf": [value, {"type": "null"}]}
            variants.append({"type": "object", "additionalProperties": False,
                             "required": ["field_path", "value"], "properties": {
                                 "field_path": {"type": "string", "pattern": "^" + path_pattern + "$"},
                                 "value": value,
                             }})
            if schema.kind == "object":
                for field in shape.fields(schema.fields, declared):
                    add(path_pattern + r"\." + re.escape(field.key), declared + "." + field.key,
                        field.value_schema, field.nullable)
            elif schema.kind == "array":
                add(path_pattern + r"\[(?:0|[1-9][0-9]*)\]", declared + "[]", schema.items)

        add("attributes", "attributes", ObjectValueSchema(kind="object", fields=self.contract.fields))
        variants.append({"type": "object", "additionalProperties": False,
                         "required": ["field_path", "value"], "properties": {
                             "field_path": {"type": "string", "const": "validation_guidance"},
                             "value": {"type": ["string", "null"]},
                         }})
        variants.append({"type": "object", "additionalProperties": False,
                         "required": ["field_path", "value"], "properties": {
                             "field_path": {"type": "string", "const": "rationale"},
                             "value": {"type": "string", "minLength": 1},
                         }})
        return {"type": "array", "minItems": 1, "items": {"anyOf": variants}}

    def validate_attributes(self, attributes: Any, *, candidate_id: str | None = None,
                            extractor_input: bool = False) -> list[dict[str, Any]]:
        """Check a record against the closed profile.

        A stored record's resolvable values may carry their resolution state,
        checked against the shared vocabularies and invariant. Extractor input
        (``extractor_input``) may not: it writes only a resolvable value's
        paper wording and other declared fields.
        """
        issues: list[dict[str, Any]] = []
        resolvable = self.resolvable_objects()
        validator_owned = self.validator_owned_paths()
        issue_limit = get_generic_profile_max_issues()
        value_limit = get_generic_profile_max_record_values()
        visited = 0

        def issue(path: str, reason: str, expected: str, actual: str, message: str) -> None:
            if len(issues) < issue_limit:
                issues.append({"candidate_id": candidate_id, "field_path": path,
                               "reason": reason, "expected": expected,
                               "actual_kind": actual, "message": message})

        try:
            encoded = canonical_json(attributes)
        except (TypeError, ValueError, RecursionError):
            issue("attributes", "invalid_json", "finite JSON", _kind(attributes),
                  "Use finite JSON values; no cyclic or non-JSON values are allowed.")
            return issues
        if len(encoded.encode("utf-8")) > get_generic_profile_max_record_bytes():
            issue("attributes", "record_size_limit", "bounded JSON", _kind(attributes),
                  "Record exceeds GENERIC_PROFILE_MAX_RECORD_BYTES; reduce the record size.")
            return issues

        def visit(value: Any, schema: ValueSchema, path: str) -> None:
            nonlocal visited
            if len(issues) >= issue_limit or visited > value_limit:
                return
            visited += 1
            if visited > value_limit:
                issue(path, "record_value_limit", "bounded record", _kind(value),
                      "Record exceeds GENERIC_PROFILE_MAX_RECORD_VALUES; reduce the record size.")
                return
            kind = schema.kind
            valid_type = (
                (kind in ("string", "enum") and type(value) is str)
                or (kind == "integer" and type(value) is int)
                or (kind == "number" and type(value) in (int, float)
                    and (type(value) is int or math.isfinite(value)))
                or (kind == "boolean" and type(value) is bool)
                or (kind == "object" and type(value) is dict)
                or (kind == "array" and type(value) is list)
            )
            if not valid_type:
                issue(path, "wrong_type", kind, _kind(value),
                      f"Supply a {kind} supported by evidence; values are not coerced.")
                return
            if schema.kind == "enum" and value not in schema.values:
                issue(path, "invalid_enum", "enum", "string",
                      "Choose one of the declared values: " + ", ".join(schema.values))
            elif schema.kind == "object" and isinstance(value, dict):
                fields = {field.key: field for field in schema.fields}
                declared = declared_value_path(path)
                identity = resolvable.get(declared)
                owned = {key for key in fields if f"{declared}.{key}" in validator_owned}
                # A validator that overrules a resolution keeps its identity, read-only, as overruled_<key>.
                hints = {overruled_key(key): key for key in identity or () if key in fields}
                system_keys = owned | (set(RESOLUTION_KEYS) | set(hints) if identity is not None else set())
                if identity is not None:
                    check_resolvable(value, identity, path)
                for key in value:
                    if key in system_keys and extractor_input:
                        issue(f"{path}.{key}", "validator_owned_field", "paper wording", _kind(value[key]),
                              "Validation fills this in; do not write it. Write the paper's wording "
                              "in the value's mention field.")
                    elif key not in fields and key not in system_keys:
                        issue(f"{path}.{key}", "undeclared_field", "declared canonical field", _kind(value[key]),
                              "Remove the undeclared field; source labels are not output keys.")
                for field in schema.fields:
                    field_path = f"{path}.{field.key}"
                    if field.key in system_keys and (extractor_input or field.key in RESOLUTION_KEYS):
                        continue
                    if field.key in owned and value.get(field.key) is None:
                        continue  # Filled in by validation; empty until it resolves.
                    if identity is not None and extractor_input and field.key == MENTION_KEY and (
                            not isinstance(value.get(MENTION_KEY), str) or not value[MENTION_KEY].strip()):
                        issue(field_path, "missing_paper_wording", "string", _kind(value.get(MENTION_KEY)),
                              "Write the paper's wording for this value.")
                        continue
                    if field.key not in value:
                        if field.required:
                            issue(field_path, "missing_required", field.value_schema.kind, "missing",
                                  "Supply evidence for this required field; do not invent a value.")
                    elif value[field.key] is None and field.nullable:
                        continue
                    else:
                        visit(value[field.key], field.value_schema, field_path)
                if not extractor_input:
                    for hint, key in hints.items():
                        if value.get(hint) is not None:
                            visit(value[hint], fields[key].value_schema, f"{path}.{hint}")
            elif schema.kind == "array" and isinstance(value, list):
                for index, item in enumerate(value):
                    if visited > value_limit or len(issues) >= issue_limit:
                        break
                    visit(item, schema.items, f"{path}[{index}]")

        def check_resolvable(value: dict[str, Any], identity: tuple[str, ...], path: str) -> None:
            if extractor_input:
                return
            for key, allowed in _VOCABULARIES.items():
                if key in value and value[key] not in allowed:
                    issue(f"{path}.{key}", "invalid_enum", "enum", _kind(value[key]),
                          "Use one of the declared values: " + ", ".join(allowed))
                    return
            for key in (VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY):
                if value.get(key) is not None and not isinstance(value[key], str):
                    issue(f"{path}.{key}", "wrong_type", "string", _kind(value[key]),
                          "Validator text is a string or null.")
                    return
            if RESOLUTION_STATE_KEY in value or LOOKUP_OUTCOME_KEY in value:
                try:
                    check_resolvable_value(value, identity_keys=identity)
                except ResolvableValueError as exc:
                    issue(path, "invalid_resolution", "resolvable value", "object", str(exc))

        visit(attributes, ObjectValueSchema(kind="object", fields=self.contract.fields), "attributes")
        return issues

    def require_attributes(self, attributes: Any, *, candidate_id: str | None = None) -> None:
        issues = self.validate_attributes(attributes, candidate_id=candidate_id)
        if issues:
            raise ProfileConformanceError(issues)

    def validate_candidate(self, candidate: dict[str, Any], *, candidate_id: str | None = None,
                           extractor_input: bool = False) -> list[dict[str, Any]]:
        allowed = {"domain_pack_id", "object_type", "class_key", "label", "classification_notes",
                   "payload", "pending_ref_id", "source_label", "description", "confidence",
                   "semantic_class", "attributes", "evidence_record_ids", "validation_guidance",
                   "rationale"}
        unknown = set(candidate) - allowed
        if unknown:
            return [{"candidate_id": candidate_id, "field_path": key,
                     "reason": "undeclared_field", "expected": "declared candidate field",
                     "actual_kind": _kind(candidate[key]),
                     "message": "No auxiliary field bag is allowed; use only the declared attributes."}
                    for key in sorted(unknown)[:get_generic_profile_max_issues()]]
        if candidate.get("validation_guidance") is not None and not isinstance(candidate["validation_guidance"], str):
            return [{"candidate_id": candidate_id, "field_path": "validation_guidance",
                     "reason": "invalid_type", "expected": "string or null",
                     "actual_kind": _kind(candidate["validation_guidance"]),
                     "message": "Validation guidance must be a short advisory sentence."}]
        if candidate.get("payload"):
            return [{"candidate_id": candidate_id, "field_path": "payload",
                     "reason": "profile_payload_forbidden", "expected": "canonical attributes",
                     "actual_kind": _kind(candidate["payload"]),
                     "message": "Profile-bound data belongs only in declared attributes; no auxiliary payload is allowed."}]
        identity = {"class_key": "generic:generic_object", "object_type": "generic_object",
                    "semantic_class": self.contract.semantic_class}
        for key, expected in identity.items():
            if candidate.get(key) != expected:
                return [{"candidate_id": candidate_id, "field_path": key,
                         "reason": "profile_identity_violation", "expected": expected,
                         "actual_kind": _kind(candidate.get(key)),
                         "message": "The saved profile fixes this identity; it cannot be changed during extraction."}]
        return self.validate_attributes(candidate.get("attributes", {}), candidate_id=candidate_id,
                                        extractor_input=extractor_input)

    def require_candidate(self, candidate: dict[str, Any], *, candidate_id: str | None = None) -> None:
        issues = self.validate_candidate(candidate, candidate_id=candidate_id)
        if issues:
            raise ProfileConformanceError(issues)

    def require_envelope(self, envelope: dict[str, Any], *, execution_receipt: dict[str, Any] | None = None,
                         agent_key: str | None = None) -> None:
        """Validate materialized or subsequently edited output without coercing data.

        The ordinary envelope schema owns structural/evidence fields. This layer
        owns the pinned identity and closed semantic attributes of every record.
        Validation never returns a normalized replacement that could drop claims.
        """
        from pydantic import ValidationError
        from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

        try:
            DomainEnvelopeExtractionResult.model_validate(envelope)
        except ValidationError as exc:
            # The canonical envelope schema is ours, not the curator's. A failure
            # here means an internal writer produced a shape the system itself
            # forbids, so it must not be reported as an Output Structure problem.
            raise EnvelopeIntegrityError([
                {"candidate_id": None, "field_path": ".".join(map(str, error["loc"])),
                 "reason": "invalid_envelope", "expected": "canonical extraction envelope",
                 "actual_kind": "invalid", "message": "Repair the extraction envelope structure."}
                for error in exc.errors(include_input=False)[:get_generic_profile_max_issues()]
            ]) from exc
        provenance = envelope.get("metadata", {}).get("provenance", {})
        self.require_receipt(provenance.get("generic_profile_ref"))
        if execution_receipt is not None and provenance.get("execution_receipt") != execution_receipt:
            raise ProfileIdentityError("Envelope executable receipt does not match the bound run")
        if agent_key is not None and provenance.get("produced_by") != agent_key:
            raise ProfileIdentityError("Envelope producer does not match the canonical agent")
        issues = []
        payload_keys = {"label", "class_key", "source_label", "description", "confidence",
                        "classification_notes", "rationale", "semantic_class", "attributes"}
        for index, obj in enumerate(envelope.get("curatable_objects", [])):
            candidate_id = obj.get("pending_ref_id")
            self.require_receipt(obj.get("metadata", {}).get("generic_profile_ref"))
            payload = obj.get("payload", {})
            if (obj.get("object_type") != "generic_object"
                    or payload.get("semantic_class") != self.contract.semantic_class
                    or payload.get("class_key", "generic:generic_object") != "generic:generic_object"
                    or obj.get("metadata", {}).get("generic_extraction", {}).get("class_key") != "generic:generic_object"):
                raise ProfileIdentityError("Envelope record identity does not match the bound profile")
            unknown = set(payload) - payload_keys
            for key in sorted(unknown):
                issues.append({"candidate_id": candidate_id,
                               "field_path": f"curatable_objects[{index}].payload.{key}",
                               "reason": "undeclared_field", "expected": "canonical payload field",
                               "actual_kind": _kind(payload[key]),
                               "message": "Do not store undeclared claims in auxiliary payload fields."})
            for issue in self.validate_attributes(payload.get("attributes", {}), candidate_id=candidate_id):
                issues.append({**issue, "field_path": f"curatable_objects[{index}].payload." + issue["field_path"]})
            if len(issues) >= get_generic_profile_max_issues():
                break
        if issues:
            raise ProfileConformanceError(issues[:get_generic_profile_max_issues()])

    def apply_curator_edit(self, attributes: dict[str, Any], field_path: str, value: Any, *,
                           actor_id: str, at: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """One curator edit of a profile record; an identity edit is a validation override.

        Editing a resolvable value's identity (one identity key, or the whole
        value with changed identity keys) goes through ``apply_curator_identity``:
        the value becomes resolved as ``curator_override`` with who and when,
        or unresolved again when the identity is cleared. Returns the record
        and the override audit (None for an ordinary edit). The paper wording
        and the validation state are never curator-editable.
        """
        parent, _, key = field_path.rpartition(".")
        resolvable = self.resolvable_objects()
        whole = resolvable.get(declared_value_path(field_path))
        identity = whole if whole is not None else resolvable.get(declared_value_path(parent))
        if whole is None and identity is not None and _not_curator_editable(key):
            raise ProfileConformanceError([_patch_issue(
                None, field_path, "The paper wording and validation state are not editable; edit the identity.")])
        if identity is None or (whole is None and key not in identity):
            return self.patch_attributes(attributes, [{"field_path": field_path, "value": value}]), None
        value_path = field_path if whole is not None else parent
        result = deepcopy(attributes)
        container = _attribute_container(result, value_path)
        if not isinstance(container, dict):
            raise ProfileConformanceError([_patch_issue(None, field_path, "Edit an existing value.")])
        if whole is not None:
            # A whole-value override changes only the identity; every other key passes through unchanged.
            if not isinstance(value, dict) or any(
                item_key not in identity and item != container.get(item_key)
                for item_key, item in value.items()
            ):
                raise ProfileConformanceError([_patch_issue(
                    None, field_path, "Only the identifier and name can be changed in a curator override.")])
            # Every identity key the value carries goes to the override, as for pack values.
            edits = {item_key: deepcopy(value[item_key]) for item_key in identity if item_key in value}
            if all(item == container.get(item_key) for item_key, item in edits.items()):
                edits = {}
        else:
            edits = {key: deepcopy(value)}
        audit = None
        if edits:
            spec = self.resolvable_specs()[declared_value_path(value_path)]
            try:
                audit = apply_curator_identity(
                    container, edits, identity_keys=identity, id_key=spec.id_key, label_key=spec.label_key,
                    actor_id=actor_id, at=at,
                )
            except ResolvableValueError as exc:
                raise ProfileConformanceError([_patch_issue(None, field_path, str(exc))]) from exc
            audit = {**audit, "value_path": value_path}
        self.require_attributes(result)
        return result, audit

    def patch_attributes(self, attributes: dict[str, Any], updates: list[dict[str, Any]],
                         *, candidate_id: str | None = None) -> dict[str, Any]:
        """Apply whole-subtree or parsed-index replacements atomically to a copy.

        No implicit deletion, sparse array creation, alias normalization, or
        literal dotted keys. Optional absent root fields may be added; nested
        containers must already exist (replace their entire subtree otherwise).
        """
        result = deepcopy(attributes)
        root_schema = ObjectValueSchema(kind="object", fields=self.contract.fields)
        for update in updates:
            path = update.get("field_path")
            if not isinstance(path, str) or not re.fullmatch(
                r"attributes(?:\.[a-z][a-z0-9_]*|\[(?:0|[1-9][0-9]*)\])*", path
            ) or "value" not in update or set(update) != {"field_path", "value"}:
                raise ProfileConformanceError([_patch_issue(candidate_id, "attributes", "Use a canonical attributes path and an explicit value.")])
            tokens = re.findall(r"\.([a-z][a-z0-9_]*)|\[([0-9]+)\]", path[len("attributes"):])
            if not tokens:
                result = deepcopy(update["value"])
                continue
            container: Any = result
            schema: ValueSchema = root_schema
            for index, (key, array_index) in enumerate(tokens):
                last = index == len(tokens) - 1
                if key and schema.kind == "object" and type(container) is dict:
                    field = next((field for field in schema.fields if field.key == key), None)
                    if field is None:
                        raise ProfileConformanceError([_patch_issue(candidate_id, path, "Use a declared canonical field, not an alias or literal dotted key.")])
                    schema = field.value_schema
                    target: Any = key
                elif array_index and schema.kind == "array" and type(container) is list:
                    if len(array_index) > len(str(len(container))):
                        raise ProfileConformanceError([_patch_issue(candidate_id, path, "Use an existing array index or replace the entire array.")])
                    target = int(array_index)
                    if target >= len(container):
                        raise ProfileConformanceError([_patch_issue(candidate_id, path, "Use an existing array index or replace the entire array.")])
                    schema = schema.items
                else:
                    raise ProfileConformanceError([_patch_issue(candidate_id, path, "Path does not match the declared structure; replace the containing subtree.")])
                if last:
                    container[target] = deepcopy(update["value"])
                else:
                    try:
                        container = container[target]
                    except (KeyError, IndexError) as exc:
                        raise ProfileConformanceError([_patch_issue(candidate_id, path, "Replace the absent containing subtree first.")]) from exc
        self.require_attributes(result, candidate_id=candidate_id)
        return result


def _not_curator_editable(key: str) -> bool:
    """The paper wording, validation state and overruled identities are never curator edits."""
    return key == MENTION_KEY or key in RESOLUTION_KEYS or key.startswith(OVERRULED_KEY_PREFIX)


def _attribute_container(attributes: dict[str, Any], path: str) -> Any:
    value: Any = {"attributes": attributes}
    for key, index in re.findall(r"([a-z][a-z0-9_]*)|\[([0-9]+)\]", path):
        if key:
            value = value.get(key) if isinstance(value, dict) else None
        else:
            value = value[int(index)] if isinstance(value, list) and int(index) < len(value) else None
    return value


def _patch_issue(candidate_id: str | None, path: str, message: str) -> dict[str, Any]:
    return {"candidate_id": candidate_id, "field_path": path, "reason": "invalid_patch_path",
            "expected": "declared path", "actual_kind": "path", "message": message}


class _ExtractorShape:
    """What the extractor writes: never a validator-owned field or a resolvable value's state."""

    def __init__(self, resolvable: dict[str, tuple[str, ...]], owned: set[str]):
        self.resolvable = resolvable
        self.owned = owned

    def fields(self, fields: list[ProfileField], path: str) -> list[ProfileField]:
        excluded = set(RESOLUTION_KEYS) if path in self.resolvable else set()
        return [field for field in fields
                if field.key not in excluded and f"{path}.{field.key}" not in self.owned]


def _object_schema(fields: list[ProfileField], path: str = "attributes",
                   shape: _ExtractorShape | None = None) -> dict[str, Any]:
    resolvable = path in shape.resolvable if shape is not None else False
    fields = shape.fields(fields, path) if shape is not None else fields
    properties = {}
    for field in fields:
        schema: dict[str, Any] = _value_schema(field.value_schema, f"{path}.{field.key}", shape)
        if field.nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
        description = field.description
        if field.source_labels:
            description += " Source labels (recognition only, never output keys): " + ", ".join(field.source_labels)
        if description:
            schema["description"] = description.strip()
        properties[field.key] = schema
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": [field.key for field in fields
                         if field.required or (resolvable and field.key == MENTION_KEY)]}


def _value_schema(schema: ValueSchema, path: str = "attributes",
                  shape: _ExtractorShape | None = None) -> dict[str, Any]:
    if schema.kind == "object":
        return _object_schema(schema.fields, path, shape)
    if schema.kind == "array":
        return {"type": "array", "items": _value_schema(schema.items, path + "[]", shape)}
    if schema.kind == "enum":
        return {"type": "string", "enum": list(schema.values)}
    return {"type": schema.kind}
