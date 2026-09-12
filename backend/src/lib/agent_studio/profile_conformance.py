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

from src.lib.openai_agents.config import (
    get_generic_profile_max_issues,
    get_generic_profile_max_record_bytes,
    get_generic_profile_max_record_values,
)
from src.schemas.agent_execution_revision import GenericProfilePin
from src.schemas.generic_extraction_profile import (
    GenericProfileContract, ObjectValueSchema, ProfileField, ValueSchema,
    canonical_json,
)


class ProfileIdentityError(ValueError):
    """The provided contract does not match the invocation's immutable receipt."""


class ProfileConformanceError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("Record does not conform to its saved output structure")
        self.issues = issues


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

    def require_receipt(self, receipt: dict[str, Any]) -> None:
        try:
            supplied = GenericProfilePin.model_validate(receipt).model_dump(mode="json")
        except ValueError as exc:
            raise ProfileIdentityError("Missing or invalid profile receipt") from exc
        if supplied != self.receipt:
            raise ProfileIdentityError("Profile receipt does not match the bound revision")

    def attributes_schema(self) -> dict[str, Any]:
        """Canonical fields only; aliases are recognition prose, never properties."""
        return _object_schema(self.contract.fields)

    def patch_schema(self) -> dict[str, Any]:
        """Typed canonical paths, including array index and subtree replacements."""
        variants = []

        def add(path_pattern: str, schema: ValueSchema, nullable: bool = False) -> None:
            value = _value_schema(schema)
            if nullable:
                value = {"anyOf": [value, {"type": "null"}]}
            variants.append({"type": "object", "additionalProperties": False,
                             "required": ["field_path", "value"], "properties": {
                                 "field_path": {"type": "string", "pattern": "^" + path_pattern + "$"},
                                 "value": value,
                             }})
            if schema.kind == "object":
                for field in schema.fields:
                    add(path_pattern + r"\." + re.escape(field.key), field.value_schema, field.nullable)
            elif schema.kind == "array":
                add(path_pattern + r"\[(?:0|[1-9][0-9]*)\]", schema.items)

        add("attributes", ObjectValueSchema(kind="object", fields=self.contract.fields))
        return {"type": "array", "minItems": 1, "items": {"anyOf": variants}}

    def validate_attributes(self, attributes: Any, *, candidate_id: str | None = None) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
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
                for key in value:
                    if key not in fields:
                        issue(f"{path}.{key}", "undeclared_field", "declared canonical field", _kind(value[key]),
                              "Remove the undeclared field; source labels are not output keys.")
                for field in schema.fields:
                    field_path = f"{path}.{field.key}"
                    if field.key not in value:
                        if field.required:
                            issue(field_path, "missing_required", field.value_schema.kind, "missing",
                                  "Supply evidence for this required field; do not invent a value.")
                    elif value[field.key] is None and field.nullable:
                        continue
                    else:
                        visit(value[field.key], field.value_schema, field_path)
            elif schema.kind == "array" and isinstance(value, list):
                for index, item in enumerate(value):
                    if visited > value_limit or len(issues) >= issue_limit:
                        break
                    visit(item, schema.items, f"{path}[{index}]")

        visit(attributes, ObjectValueSchema(kind="object", fields=self.contract.fields), "attributes")
        return issues

    def require_attributes(self, attributes: Any, *, candidate_id: str | None = None) -> None:
        issues = self.validate_attributes(attributes, candidate_id=candidate_id)
        if issues:
            raise ProfileConformanceError(issues)

    def validate_candidate(self, candidate: dict[str, Any], *, candidate_id: str | None = None) -> list[dict[str, Any]]:
        allowed = {"domain_pack_id", "object_type", "class_key", "label", "classification_notes",
                   "payload", "pending_ref_id", "source_label", "description", "confidence",
                   "semantic_class", "attributes", "evidence_record_ids"}
        unknown = set(candidate) - allowed
        if unknown:
            return [{"candidate_id": candidate_id, "field_path": key,
                     "reason": "undeclared_field", "expected": "declared candidate field",
                     "actual_kind": _kind(candidate[key]),
                     "message": "No auxiliary field bag is allowed; use only the declared attributes."}
                    for key in sorted(unknown)[:get_generic_profile_max_issues()]]
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
        return self.validate_attributes(candidate.get("attributes", {}), candidate_id=candidate_id)

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
            raise ProfileConformanceError([
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
                        "classification_notes", "semantic_class", "attributes"}
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


def _patch_issue(candidate_id: str | None, path: str, message: str) -> dict[str, Any]:
    return {"candidate_id": candidate_id, "field_path": path, "reason": "invalid_patch_path",
            "expected": "declared path", "actual_kind": "path", "message": message}


def _object_schema(fields: list[ProfileField]) -> dict[str, Any]:
    properties = {}
    for field in fields:
        schema: dict[str, Any] = _value_schema(field.value_schema)
        if field.nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
        description = field.description
        if field.source_labels:
            description += " Source labels (recognition only, never output keys): " + ", ".join(field.source_labels)
        if description:
            schema["description"] = description.strip()
        properties[field.key] = schema
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": [field.key for field in fields if field.required]}


def _value_schema(schema: ValueSchema) -> dict[str, Any]:
    if schema.kind == "object":
        return _object_schema(schema.fields)
    if schema.kind == "array":
        return {"type": "array", "items": _value_schema(schema.items)}
    if schema.kind == "enum":
        return {"type": "string", "enum": list(schema.values)}
    return {"type": schema.kind}
