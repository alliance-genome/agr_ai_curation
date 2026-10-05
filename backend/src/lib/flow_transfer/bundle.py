"""The flow export bundle (format version 1): models, canonical hash and integrity checks.

Main AI Curation builds a bundle from canonical rows. The benchmark resolver trusts
nothing in it until the signature verifies (``signing.verify_bundle``) and
``check_bundle`` confirms it is exactly the closure of the flow: every custom step's
pinned revision, and every output structure revision those revisions name, nothing more.
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.schemas.agent_execution_revision import AgentExecutionSnapshot
from src.schemas.flows import FlowDefinition
from src.schemas.generic_extraction_profile import canonical_json, normalize_profile_contract

FORMAT = "aic-flow-export"
FORMAT_VERSION = 1
FLOW_BUNDLE_MAX_BYTES = 8 * 1024 * 1024
MAX_AGENTS = 64
MAX_REVISIONS_PER_AGENT = 64
MAX_PROFILE_REVISIONS = 64

Fingerprint = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]


class InvalidBundle(ValueError):
    """Malformed, or not exactly the closure it claims. Never shown to curators."""


class BundleTooLarge(ValueError):
    """The canonical bundle is over FLOW_BUNDLE_MAX_BYTES."""


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BundleSource(_Part):
    issuer: str = Field(min_length=1)
    app_version: str = Field(min_length=1)


class ExportedFor(_Part):
    iss: str = Field(min_length=1)
    sub: str = Field(min_length=1)


class BundleFlow(_Part):
    source_flow_id: UUID
    source_version: Fingerprint
    owned: bool
    name: str = Field(min_length=1)
    description: str | None
    definition: dict[str, Any]


class BundleAgentRevision(_Part):
    source_revision_id: UUID
    revision: int = Field(ge=1)
    fingerprint: Fingerprint
    snapshot: dict[str, Any]


class BundleAgent(_Part):
    source_agent_id: UUID
    source_agent_key: str = Field(pattern=r"^ca_")
    name: str = Field(min_length=1)
    description: str | None
    icon: str
    category: str | None
    revisions: list[BundleAgentRevision] = Field(min_length=1, max_length=MAX_REVISIONS_PER_AGENT)


class BundleProfileRevision(_Part):
    source_revision_id: UUID
    revision: int = Field(ge=1)
    fingerprint: Fingerprint
    contract: dict[str, Any]


class BundleProfile(_Part):
    source_profile_id: UUID
    revisions: list[BundleProfileRevision] = Field(min_length=1, max_length=MAX_PROFILE_REVISIONS)


class FlowBundle(_Part):
    format: Literal["aic-flow-export"]
    format_version: Literal[1]
    source: BundleSource
    exported_for: ExportedFor
    exported_at: str = Field(min_length=1)
    flow: BundleFlow
    agents: list[BundleAgent] = Field(max_length=MAX_AGENTS)
    profiles: list[BundleProfile]


def canonical_bundle_bytes(bundle_json: dict[str, Any]) -> bytes:
    return canonical_json(bundle_json).encode("utf-8")


def bundle_sha256(bundle_json: dict[str, Any]) -> str:
    """Hash of the JSON value as it travels, so every hop hashes the same bytes."""
    return "sha256:" + hashlib.sha256(canonical_bundle_bytes(bundle_json)).hexdigest()


@dataclass(frozen=True)
class CheckedBundle:
    bundle: FlowBundle
    sha256: str
    # node id -> (source agent id, source revision id) for every custom step
    pins: dict[str, tuple[UUID, UUID]]
    # node id -> the step's receipt exactly as exported (moving selected-fields layouts needs it)
    receipts: dict[str, dict[str, Any]]
    # source revision id -> its verified snapshot
    snapshots: dict[UUID, AgentExecutionSnapshot]


def check_bundle(bundle_json: dict[str, Any]) -> CheckedBundle:
    """Everything that needs neither a database nor a key: size, schema, closure, fingerprints."""
    if len(canonical_bundle_bytes(bundle_json)) > FLOW_BUNDLE_MAX_BYTES:
        raise BundleTooLarge("bundle")
    try:
        bundle = FlowBundle.model_validate(bundle_json)
        definition = FlowDefinition.model_validate(bundle.flow.definition)
    except ValueError:
        raise InvalidBundle("schema") from None
    if sum(len(profile.revisions) for profile in bundle.profiles) > MAX_PROFILE_REVISIONS:
        raise InvalidBundle("bounds")
    if len({agent.source_agent_id for agent in bundle.agents}) != len(bundle.agents):
        raise InvalidBundle("duplicate agent")
    revisions: dict[UUID, tuple[BundleAgent, BundleAgentRevision]] = {}
    for agent in bundle.agents:
        for revision in agent.revisions:
            if revision.source_revision_id in revisions:
                raise InvalidBundle("duplicate revision")
            revisions[revision.source_revision_id] = (agent, revision)
    snapshots: dict[UUID, AgentExecutionSnapshot] = {}
    for revision_id, (_, revision) in revisions.items():
        try:
            saved = AgentExecutionSnapshot.model_validate(revision.snapshot)
        except ValueError:
            raise InvalidBundle("snapshot") from None
        if saved.fingerprint() != revision.fingerprint:
            raise InvalidBundle("snapshot fingerprint")
        snapshots[revision_id] = saved
    pins: dict[str, tuple[UUID, UUID]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for node in definition.nodes:
        if not node.data.agent_id.startswith("ca_"):
            continue
        receipt = node.data.execution_receipt
        if receipt is None or node.data.agent_revision_id != receipt.agent_revision_id:
            raise InvalidBundle("unpinned step")
        found = revisions.get(receipt.agent_revision_id)
        if found is None:
            raise InvalidBundle("missing revision")
        agent, revision = found
        if (receipt.agent_id != agent.source_agent_id or receipt.agent_key != agent.source_agent_key
                or node.data.agent_id != agent.source_agent_key
                or receipt.revision != revision.revision or receipt.fingerprint != revision.fingerprint
                or receipt.output_contract != snapshots[revision.source_revision_id].output_contract):
            raise InvalidBundle("receipt")
        pins[node.id] = (agent.source_agent_id, revision.source_revision_id)
        receipts[node.id] = receipt.model_dump(mode="json")
    if {revision_id for _, revision_id in pins.values()} != set(revisions):
        raise InvalidBundle("extra revision")
    expected = {}
    for saved in snapshots.values():
        ref = saved.output_contract.generic_profile_ref
        if ref is not None:
            expected[ref.profile_revision_id] = ref
    bundled: dict[UUID, tuple[BundleProfile, BundleProfileRevision]] = {}
    for profile in bundle.profiles:
        for revision in profile.revisions:
            if revision.source_revision_id in bundled:
                raise InvalidBundle("duplicate profile revision")
            bundled[revision.source_revision_id] = (profile, revision)
    if set(bundled) != set(expected):
        raise InvalidBundle("profile closure")
    for revision_id, (profile, revision) in bundled.items():
        ref = expected[revision_id]
        try:
            parsed = normalize_profile_contract(revision.contract)
        except ValueError:
            raise InvalidBundle("profile contract") from None
        if (parsed.fingerprint() != revision.fingerprint or ref.fingerprint != revision.fingerprint
                or ref.profile_id != profile.source_profile_id or ref.revision != revision.revision):
            raise InvalidBundle("profile identity")
    return CheckedBundle(bundle=bundle, sha256=bundle_sha256(bundle_json), pins=pins,
                         receipts=receipts, snapshots=snapshots)
