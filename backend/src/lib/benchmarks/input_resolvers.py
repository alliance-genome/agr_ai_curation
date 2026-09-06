"""Allowlisted materialization of immutable benchmark runtime inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Protocol, TypeVar, runtime_checkable

from pydantic import Field, RootModel, ValidationError

from src.lib.security.redaction import active_secret_redaction
from src.lib.openai_agents.config import get_benchmark_source_discovery_max_choices
from src.schemas.benchmark_sources import (
    BenchmarkSourceArtifactChoice,
    BenchmarkSourceDiscoveryPage,
    BenchmarkSourceDiscoveryRequest,
    BenchmarkSourcePreparationRequest,
)

from .document_inputs import decode_frozen_document
from .models import BenchmarkInputReference, FrozenStrictModel, ResolvedBenchmarkPlan

_RESOLVER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DelegatedAuthorizationCapability(str, Enum):
    """Whether a resolver may consume request-local delegated authorization."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    UNSUPPORTED = "unsupported"


class DelegatedSourceAuthorization:
    """Opaque request-local bearer whose display and serialization are redacted."""

    __slots__ = ("__bearer",)

    def __init__(self, bearer: str) -> None:
        if not bearer:
            raise ValueError("delegated source bearer is required")
        self.__bearer = bearer

    def reveal(self) -> str:
        """Reveal only at the selected resolver's outbound request boundary."""

        return self.__bearer

    def redaction_scope(self) -> AbstractContextManager[None]:
        """Scrub the bearer mechanically while the selected resolver may use it."""

        return active_secret_redaction(self.__bearer)

    def __repr__(self) -> str:
        return "DelegatedSourceAuthorization('[Filtered]')"

    def __str__(self) -> str:
        return "[Filtered]"


@dataclass(frozen=True)
class BenchmarkSourceRequestContext:
    """Non-durable identities available during synchronous materialization."""

    principal_subject: str
    delegated_authorization: DelegatedSourceAuthorization | None = None

    def without_delegated_authorization(self) -> BenchmarkSourceRequestContext:
        return BenchmarkSourceRequestContext(principal_subject=self.principal_subject)


class BenchmarkSourceError(RuntimeError):
    """Stable resolver failure safe to map onto the public source API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BenchmarkResolverRegistrationError(ValueError):
    """Stable startup/configuration failure for an invalid resolver catalog."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CheckedInFixtureReference(RootModel[str]):
    """Repository-relative JSON fixture path."""

    root: Annotated[str, Field(min_length=1, max_length=1024)]

    def canonical_path(self) -> Path:
        path = Path(self.root)
        if path.is_absolute() or path.suffix != ".json" or ".." in path.parts:
            raise ValueError("fixture reference must be a relative JSON path")
        return path


class BenchmarkSourceMetadata(FrozenStrictModel):
    """Fixed-shape metadata keeps materialization responses bounded."""

    content_type: str = Field(min_length=1, max_length=128)
    content_bytes: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=255)


class BenchmarkSourceProvenance(FrozenStrictModel):
    """Bounded provenance receipt for one immutable source."""

    resolver: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=1024)
    version: str = Field(min_length=1, max_length=255)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MaterializedBenchmarkInput(FrozenStrictModel):
    """UTF-8 content frozen with its authoritative identity and receipt."""

    resolver: str
    reference: str
    version: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content: str = Field(min_length=1)
    metadata: BenchmarkSourceMetadata
    provenance: BenchmarkSourceProvenance


class MaterializedBenchmarkCaseInput(FrozenStrictModel):
    """One suite case paired with its immutable runtime source."""

    case_id: str
    source: MaterializedBenchmarkInput


class MaterializedBenchmarkPlanInputs(FrozenStrictModel):
    """All case inputs resolved before a plan may be handed to a queue."""

    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cases: tuple[MaterializedBenchmarkCaseInput, ...]


@runtime_checkable
class BenchmarkInputResolver(Protocol):
    """Trusted deployment-owned source resolver contract."""

    resolver_id: str
    reference_schema: type[RootModel[str]]
    delegated_authorization: DelegatedAuthorizationCapability

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput: ...


@runtime_checkable
class DiscoverableBenchmarkSource(Protocol):
    """Optional metadata-only navigation on a registered resolver."""

    async def discover(
        self, selection: BenchmarkSourceDiscoveryRequest, *, max_choices: int,
        request_context: BenchmarkSourceRequestContext,
    ) -> BenchmarkSourceDiscoveryPage: ...


@runtime_checkable
class PreparableBenchmarkSource(Protocol):
    """Optional initial download, without an invented expected digest."""

    async def prepare(
        self, validated_reference: str, *, max_bytes: int,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput: ...


_SourceResult = TypeVar("_SourceResult")


class BenchmarkInputResolverCatalog:
    """Immutable allowlist of resolver implementations registered at startup."""

    def __init__(
        self,
        resolvers: Iterable[BenchmarkInputResolver],
        *,
        timeout_seconds: float,
        max_input_bytes: int,
    ) -> None:
        if timeout_seconds <= 0 or max_input_bytes <= 0:
            raise ValueError("benchmark source limits must be positive")
        registered = self._validated_registrations(resolvers)
        self._resolvers = registered
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes

    @staticmethod
    def _validated_registrations(
        resolvers: Iterable[BenchmarkInputResolver],
    ) -> dict[str, BenchmarkInputResolver]:
        registered: dict[str, BenchmarkInputResolver] = {}
        for resolver in resolvers:
            resolver_id = resolver.resolver_id
            if not _RESOLVER_ID_PATTERN.fullmatch(resolver_id):
                raise BenchmarkResolverRegistrationError(
                    "invalid_resolver_registration",
                    f"Invalid benchmark input resolver ID: {resolver_id}",
                )
            if resolver_id in registered:
                raise BenchmarkResolverRegistrationError(
                    "duplicate_resolver",
                    f"Duplicate benchmark input resolver registration: {resolver_id}",
                )
            if not isinstance(
                getattr(resolver, "delegated_authorization", None),
                DelegatedAuthorizationCapability,
            ):
                raise BenchmarkResolverRegistrationError(
                    "invalid_resolver_registration",
                    f"Resolver {resolver_id} must declare delegated authorization support",
                )
            registered[resolver_id] = resolver
        return registered

    @classmethod
    def validate_registration(
        cls, resolvers: Iterable[BenchmarkInputResolver]
    ) -> None:
        """Validate startup-owned resolver IDs without materializing sources."""

        cls._validated_registrations(resolvers)

    @property
    def resolver_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._resolvers))

    def validate_delegated_selection(
        self,
        references: Iterable[
            BenchmarkInputReference | BenchmarkSourceDiscoveryRequest
            | BenchmarkSourcePreparationRequest
        ],
        request_context: BenchmarkSourceRequestContext,
    ) -> None:
        """Fail before I/O unless one credential maps to exactly one resolver identity."""

        selected = {
            reference.resolver: self._resolvers.get(reference.resolver)
            for reference in references
        }
        capable = {
            resolver_id: resolver
            for resolver_id, resolver in selected.items()
            if resolver is not None
            and resolver.delegated_authorization
            is not DelegatedAuthorizationCapability.UNSUPPORTED
        }
        if len(capable) > 1:
            raise BenchmarkSourceError(
                "invalid_delegated_authorization",
                "A delegated source credential may select only one resolver identity",
            )
        credential = request_context.delegated_authorization
        if credential is not None and not capable:
            raise BenchmarkSourceError(
                "unexpected_delegated_authorization",
                "Delegated source authorization is not accepted for the selected source",
            )
        if credential is None and any(
            resolver.delegated_authorization
            is DelegatedAuthorizationCapability.REQUIRED
            for resolver in capable.values()
        ):
            raise BenchmarkSourceError(
                "missing_delegated_authorization",
                "Delegated source authorization is required for the selected source",
            )

    def _selection_resolver(self, selection, request_context):
        resolver = self._resolvers.get(selection.resolver)
        if resolver is None:
            raise BenchmarkSourceError("unknown_resolver", "Benchmark input resolver is not registered")
        self.validate_delegated_selection((selection,), request_context)
        return resolver

    async def _invoke_selection(
        self, operation: Awaitable[_SourceResult], context: BenchmarkSourceRequestContext,
    ) -> _SourceResult:
        credential = context.delegated_authorization
        failure = None
        with credential.redaction_scope() if credential is not None else nullcontext():
            try:
                return await asyncio.wait_for(operation, timeout=self._timeout_seconds)
            except BenchmarkSourceError as exc:
                messages = {
                    "invalid_reference": "Benchmark input reference is invalid",
                    "forbidden_source": "The selected benchmark source denied access",
                    "missing_source": "The selected benchmark source was not found",
                    "oversize_payload": "Benchmark source exceeds the configured limit",
                    "source_unavailable": "Benchmark input source is unavailable",
                }
                code = exc.code if exc.code in messages else "source_unavailable"
                failure = BenchmarkSourceError(code, messages[code])
            except Exception:
                failure = BenchmarkSourceError("source_unavailable", "Benchmark input source is unavailable")
        # Raise outside the adapter exception handler: do not retain its payload.
        raise failure

    async def discover(
        self, selection: BenchmarkSourceDiscoveryRequest, *,
        request_context: BenchmarkSourceRequestContext,
    ) -> BenchmarkSourceDiscoveryPage:
        resolver = self._selection_resolver(selection, request_context)
        if not isinstance(resolver, DiscoverableBenchmarkSource):
            raise BenchmarkSourceError("unsupported_operation", "Source discovery is not supported")
        page = await self._invoke_selection(
            resolver.discover(selection, max_choices=get_benchmark_source_discovery_max_choices(),
                              request_context=request_context), request_context,
        )
        try:
            # Revalidate even constructed model instances from trusted adapters.
            page = BenchmarkSourceDiscoveryPage.model_validate(page.model_dump())
            for choice in page.choices:
                if isinstance(choice, BenchmarkSourceArtifactChoice) and choice.reference is not None:
                    resolver.reference_schema.model_validate(choice.reference)
            return page
        except Exception:
            pass
        raise BenchmarkSourceError("source_unavailable", "Source discovery returned an invalid page")

    async def prepare(
        self, selection: BenchmarkSourcePreparationRequest, *,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput:
        resolver = self._selection_resolver(selection, request_context)
        if not isinstance(resolver, PreparableBenchmarkSource):
            raise BenchmarkSourceError("unsupported_operation", "Source preparation is not supported")
        validated = None
        try:
            validated = resolver.reference_schema.model_validate(selection.reference).root
        except (ValidationError, ValueError):
            pass
        if validated is None:
            raise BenchmarkSourceError("invalid_reference", "Benchmark input reference is invalid")
        result = await self._invoke_selection(
            resolver.prepare(validated, max_bytes=self._max_input_bytes,
                             request_context=request_context), request_context,
        )
        try:
            result = MaterializedBenchmarkInput.model_validate(result.model_dump())
            # These are actual returned pins, not expectations invented before I/O.
            pins = BenchmarkInputReference(resolver=selection.resolver, reference=selection.reference,
                                           version=result.version, digest=result.digest)
        except Exception:
            pass
        else:
            verified = self._verify_materialized(pins, result)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(decode_frozen_document, verified.content.encode("utf-8"),
                                      content_type=verified.metadata.content_type),
                    timeout=self._timeout_seconds,
                )
                return verified
            except Exception:
                pass
        raise BenchmarkSourceError("source_unavailable", "Source preparation returned an invalid document")

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        *,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput:
        resolver = self._resolvers.get(reference.resolver)
        if resolver is None:
            raise BenchmarkSourceError(
                "unknown_resolver", "Benchmark input resolver is not registered"
            )
        if (
            resolver.delegated_authorization
            is DelegatedAuthorizationCapability.REQUIRED
            and request_context.delegated_authorization is None
        ):
            raise BenchmarkSourceError(
                "missing_delegated_authorization",
                "Delegated source authorization is required for the selected source",
            )
        try:
            validated = resolver.reference_schema.model_validate(reference.reference)
            validated_reference = validated.root
            if isinstance(validated, CheckedInFixtureReference):
                validated.canonical_path()
        except (ValidationError, ValueError) as exc:
            raise BenchmarkSourceError(
                "invalid_reference", "Benchmark input reference is invalid"
            ) from exc

        result: MaterializedBenchmarkInput | None = None
        failure: BenchmarkSourceError | None = None
        credential = (
            request_context.delegated_authorization
            if resolver.delegated_authorization
            is not DelegatedAuthorizationCapability.UNSUPPORTED
            else None
        )
        redaction_scope = (
            credential.redaction_scope() if credential is not None else nullcontext()
        )
        with redaction_scope:
            try:
                result = await asyncio.wait_for(
                    resolver.materialize(
                        reference,
                        validated_reference,
                        max_bytes=self._max_input_bytes,
                        request_context=(
                            request_context.without_delegated_authorization()
                            if resolver.delegated_authorization
                            is DelegatedAuthorizationCapability.UNSUPPORTED
                            else request_context
                        ),
                    ),
                    timeout=self._timeout_seconds,
                )
            except BenchmarkSourceError as exc:
                if (
                    resolver.delegated_authorization
                    is DelegatedAuthorizationCapability.UNSUPPORTED
                ):
                    raise
                safe_code = (
                    exc.code
                    if exc.code
                    in {
                        "forbidden_source",
                        "missing_source",
                        "oversize_payload",
                        "source_unavailable",
                    }
                    else "source_unavailable"
                )
                safe_message = {
                    "forbidden_source": "The selected benchmark source denied access",
                    "missing_source": "The selected benchmark source was not found",
                    "oversize_payload": "Benchmark input exceeds the materialization limit",
                    "source_unavailable": "Benchmark input source is unavailable",
                }[safe_code]
                raise BenchmarkSourceError(safe_code, safe_message) from None
            except TimeoutError:
                failure = BenchmarkSourceError(
                    "source_unavailable", "Benchmark input source timed out"
                )
            except Exception:
                failure = BenchmarkSourceError(
                    "source_unavailable", "Benchmark input source is unavailable"
                )
        if failure is not None:
            raise failure
        if result is None:
            raise BenchmarkSourceError(
                "source_unavailable", "Benchmark resolver returned an invalid result"
            )

        return self._verify_materialized(reference, result)

    def _verify_materialized(
        self, reference: BenchmarkInputReference, result: MaterializedBenchmarkInput,
    ) -> MaterializedBenchmarkInput:
        verification_failure: BenchmarkSourceError | None = None
        try:
            if (
                result.resolver != reference.resolver
                or result.reference != reference.reference
            ):
                raise BenchmarkSourceError(
                    "source_unavailable",
                    "Benchmark resolver returned inconsistent identity",
                )
            provenance = result.provenance
            if (
                provenance.resolver != result.resolver
                or provenance.reference != result.reference
                or provenance.version != result.version
                or provenance.digest != result.digest
            ):
                raise BenchmarkSourceError(
                    "source_unavailable",
                    "Benchmark resolver returned inconsistent provenance",
                )
            content_bytes = result.content.encode("utf-8")
            if len(content_bytes) != result.metadata.content_bytes:
                raise BenchmarkSourceError(
                    "source_unavailable",
                    "Benchmark resolver returned inconsistent content size",
                )
            actual_digest = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
            if actual_digest != result.digest:
                raise BenchmarkSourceError(
                    "source_unavailable",
                    "Benchmark resolver returned inconsistent content digest",
                )
            if result.version != reference.version:
                raise BenchmarkSourceError(
                    "version_conflict", "Benchmark input version does not match the source"
                )
            if result.digest != reference.digest:
                raise BenchmarkSourceError(
                    "digest_conflict", "Benchmark input digest does not match the source"
                )
            if len(content_bytes) > self._max_input_bytes:
                raise BenchmarkSourceError(
                    "oversize_payload", "Benchmark input exceeds the materialization limit"
                )
        except BenchmarkSourceError:
            raise
        except Exception:
            verification_failure = BenchmarkSourceError(
                "source_unavailable", "Benchmark resolver returned an invalid result"
            )
        if verification_failure is not None:
            raise verification_failure
        return result


async def materialize_plan_inputs(
    plan: ResolvedBenchmarkPlan,
    catalog: BenchmarkInputResolverCatalog,
    *,
    request_context: BenchmarkSourceRequestContext,
    max_submission_bytes: int,
) -> MaterializedBenchmarkPlanInputs:
    """Resolve every case completely before returning a queueable input bundle."""

    cases: list[MaterializedBenchmarkCaseInput] = []
    catalog.validate_delegated_selection(
        (case.input for case in plan.cases), request_context
    )
    total_bytes = 0
    for case in plan.cases:
        source = await catalog.materialize(
            case.input, request_context=request_context
        )
        total_bytes += source.metadata.content_bytes
        if total_bytes > max_submission_bytes:
            raise BenchmarkSourceError(
                "oversize_submission",
                "Materialized benchmark submission exceeds the aggregate limit",
            )
        cases.append(MaterializedBenchmarkCaseInput(case_id=case.case_id, source=source))
    return MaterializedBenchmarkPlanInputs(
        plan_digest=plan.plan_digest, cases=tuple(cases)
    )


class CheckedInFixtureResolver:
    """Materialize only registered suite inputs beneath the benchmark root."""

    resolver_id = "checked_in_fixture"
    reference_schema = CheckedInFixtureReference
    delegated_authorization = DelegatedAuthorizationCapability.UNSUPPORTED

    def __init__(self, root: Path, *, allowed_references: Iterable[str]) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._allowed_references = frozenset(allowed_references)

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput:
        return await asyncio.to_thread(
            self._materialize_sync,
            reference,
            validated_reference,
            max_bytes=max_bytes,
            principal_subject=request_context.principal_subject,
        )

    def _materialize_sync(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        principal_subject: str,
    ) -> MaterializedBenchmarkInput:
        del principal_subject
        if validated_reference not in self._allowed_references:
            raise BenchmarkSourceError(
                "invalid_reference", "Benchmark fixture is not a registered suite input"
            )
        path = (self._root / validated_reference).resolve(strict=False)
        if not path.is_relative_to(self._root):
            raise BenchmarkSourceError(
                "invalid_reference", "Fixture reference escapes the benchmark root"
            )
        try:
            with path.open("rb") as handle:
                payload = handle.read(max_bytes + 1)
        except FileNotFoundError as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Benchmark fixture is unavailable"
            ) from exc
        except OSError as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Benchmark fixture could not be read"
            ) from exc
        if len(payload) > max_bytes:
            raise BenchmarkSourceError(
                "oversize_payload", "Benchmark input exceeds the materialization limit"
            )
        try:
            content = payload.decode("utf-8")
            decode_frozen_document(payload, content_type="application/json")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Benchmark fixture is not valid UTF-8 JSON"
            ) from exc
        except ValueError as exc:
            raise BenchmarkSourceError(
                "invalid_reference", "Benchmark fixture must contain extracted document elements"
            ) from exc
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        provenance = BenchmarkSourceProvenance(
            resolver=self.resolver_id,
            reference=reference.reference,
            version=reference.version,
            digest=digest,
        )
        return MaterializedBenchmarkInput(
            resolver=self.resolver_id,
            reference=reference.reference,
            version=reference.version,
            digest=digest,
            content=content,
            metadata=BenchmarkSourceMetadata(
                content_type="application/json",
                content_bytes=len(payload),
                title=path.name,
            ),
            provenance=provenance,
        )


__all__ = [
    "DiscoverableBenchmarkSource",
    "PreparableBenchmarkSource",
    "BenchmarkInputResolver",
    "BenchmarkInputResolverCatalog",
    "BenchmarkSourceRequestContext",
    "BenchmarkResolverRegistrationError",
    "BenchmarkSourceError",
    "BenchmarkSourceMetadata",
    "BenchmarkSourceProvenance",
    "DelegatedAuthorizationCapability",
    "DelegatedSourceAuthorization",
    "CheckedInFixtureReference",
    "CheckedInFixtureResolver",
    "MaterializedBenchmarkInput",
    "MaterializedBenchmarkCaseInput",
    "MaterializedBenchmarkPlanInputs",
    "materialize_plan_inputs",
]
