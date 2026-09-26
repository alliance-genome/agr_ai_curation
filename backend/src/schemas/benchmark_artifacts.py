"""Canonical stored benchmark output: scientific evidence and ledger references."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.schemas.cost_ledger import CostLedgerReference


class ArtifactInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    invocation_id: UUID
    accounting_reference: CostLedgerReference


class BenchmarkArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[2] = 2
    output: dict[str, Any]
    invocations: tuple[ArtifactInvocation, ...]
