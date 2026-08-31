"""Allowlisted materialization of immutable benchmark runtime inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, RootModel, ValidationError

from .models import BenchmarkInputReference, FrozenStrictModel, ResolvedBenchmarkPlan

_RESOLVER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


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

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        principal_subject: str,
    ) -> MaterializedBenchmarkInput: ...


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

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        *,
        principal_subject: str,
    ) -> MaterializedBenchmarkInput:
        resolver = self._resolvers.get(reference.resolver)
        if resolver is None:
            raise BenchmarkSourceError(
                "unknown_resolver", "Benchmark input resolver is not registered"
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
        try:
            result = await asyncio.wait_for(
                resolver.materialize(
                    reference,
                    validated_reference,
                    max_bytes=self._max_input_bytes,
                    principal_subject=principal_subject,
                ),
                timeout=self._timeout_seconds,
            )
        except BenchmarkSourceError:
            raise
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
    principal_subject: str,
) -> MaterializedBenchmarkPlanInputs:
    """Resolve every case completely before returning a queueable input bundle."""

    cases: list[MaterializedBenchmarkCaseInput] = []
    for case in plan.cases:
        source = await catalog.materialize(
            case.input, principal_subject=principal_subject
        )
        cases.append(MaterializedBenchmarkCaseInput(case_id=case.case_id, source=source))
    return MaterializedBenchmarkPlanInputs(
        plan_digest=plan.plan_digest, cases=tuple(cases)
    )


class CheckedInFixtureResolver:
    """Materialize only registered suite inputs beneath the benchmark root."""

    resolver_id = "checked_in_fixture"
    reference_schema = CheckedInFixtureReference

    def __init__(self, root: Path, *, allowed_references: Iterable[str]) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._allowed_references = frozenset(allowed_references)

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        principal_subject: str,
    ) -> MaterializedBenchmarkInput:
        return await asyncio.to_thread(
            self._materialize_sync,
            reference,
            validated_reference,
            max_bytes=max_bytes,
            principal_subject=principal_subject,
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
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Benchmark fixture is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise BenchmarkSourceError(
                "invalid_reference", "Benchmark fixture input must contain an object"
            )
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
    "BenchmarkInputResolver",
    "BenchmarkInputResolverCatalog",
    "BenchmarkResolverRegistrationError",
    "BenchmarkSourceError",
    "BenchmarkSourceMetadata",
    "BenchmarkSourceProvenance",
    "CheckedInFixtureReference",
    "CheckedInFixtureResolver",
    "MaterializedBenchmarkInput",
    "MaterializedBenchmarkCaseInput",
    "MaterializedBenchmarkPlanInputs",
    "materialize_plan_inputs",
]
