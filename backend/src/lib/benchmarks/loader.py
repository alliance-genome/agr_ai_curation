"""Side-effect-free loading and reference validation for checked-in benchmarks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import yaml
from pydantic import ValidationError

from .models import BenchmarkProfile, LoadedBenchmarkCase
from .scoring import ScorerConfigurationError, validate_scorer_reference


class BenchmarkCatalogError(ValueError):
    """A checked-in benchmark definition is invalid or ambiguous."""


@dataclass(frozen=True)
class LoadedBenchmarkProfile:
    profile: BenchmarkProfile
    cases: tuple[LoadedBenchmarkCase, ...]
    source_path: Path


def _load_mapping(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkCatalogError(
            f"Unable to load benchmark definition {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BenchmarkCatalogError(
            f"Benchmark definition {path} must contain an object"
        )
    return value


def _resolve_file(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BenchmarkCatalogError(
            f"Benchmark path escapes catalog root: {relative_path}"
        ) from exc
    if not candidate.is_file():
        raise BenchmarkCatalogError(f"Benchmark file does not exist: {relative_path}")
    return candidate


def _load_json(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkCatalogError(f"Invalid benchmark JSON {path}: {exc}") from exc


class BenchmarkCatalog:
    """Validated profiles and fixtures rooted at an arbitrary project directory."""

    def __init__(
        self,
        root: Path,
        *,
        agent_ids: Iterable[str],
        flow_ids: Iterable[str],
        route_validator: Callable[[str, str], object],
    ) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self._agent_ids = set(agent_ids)
        self._flow_ids = set(flow_ids)
        self._route_validator = route_validator
        self._loaded = self._load()

    @property
    def profiles(self) -> tuple[LoadedBenchmarkProfile, ...]:
        return self._loaded

    def get_profile(self, profile_id: str) -> LoadedBenchmarkProfile:
        for loaded in self._loaded:
            if loaded.profile.profile_id == profile_id:
                return loaded
        raise BenchmarkCatalogError(f"Unknown benchmark profile: {profile_id}")

    def validate_route(self, model: str, provider: str) -> None:
        try:
            self._route_validator(model, provider)
        except (LookupError, RuntimeError, ValueError) as exc:
            raise BenchmarkCatalogError(
                f"Invalid route {provider}/{model}: {exc}"
            ) from exc

    def _load(self) -> tuple[LoadedBenchmarkProfile, ...]:
        profile_dir = self.root / "profiles"
        if not profile_dir.is_dir():
            raise BenchmarkCatalogError(
                f"Benchmark profile directory does not exist: {profile_dir}"
            )
        loaded_profiles: list[LoadedBenchmarkProfile] = []
        seen_profile_ids: set[str] = set()
        seen_case_definitions: dict[str, tuple[str, str]] = {}
        for source_path in sorted(profile_dir.glob("*.yaml")):
            try:
                profile = BenchmarkProfile.model_validate(_load_mapping(source_path))
            except ValidationError as exc:
                raise BenchmarkCatalogError(
                    f"Invalid benchmark profile {source_path}: {exc}"
                ) from exc
            if profile.profile_id in seen_profile_ids:
                raise BenchmarkCatalogError(
                    f"Duplicate benchmark profile ID: {profile.profile_id}"
                )
            seen_profile_ids.add(profile.profile_id)
            known_targets = (
                self._agent_ids if profile.target.kind == "agent" else self._flow_ids
            )
            if profile.target.id not in known_targets:
                raise BenchmarkCatalogError(
                    f"Unknown {profile.target.kind} target '{profile.target.id}' in {profile.profile_id}"
                )
            for route in profile.routes:
                self.validate_route(route.model, route.provider)
            for scorer in profile.scorers:
                try:
                    validate_scorer_reference(scorer)
                except ScorerConfigurationError as exc:
                    raise BenchmarkCatalogError(
                        f"Invalid scorer in {profile.profile_id}: {exc}"
                    ) from exc
            cases: list[LoadedBenchmarkCase] = []
            for case_ref in profile.cases:
                definition = (case_ref.fixture, case_ref.expected)
                existing = seen_case_definitions.get(case_ref.case_id)
                if existing is not None and existing != definition:
                    raise BenchmarkCatalogError(
                        f"Case ID '{case_ref.case_id}' has conflicting fixture references"
                    )
                seen_case_definitions[case_ref.case_id] = definition
                fixture_path = _resolve_file(self.root, case_ref.fixture)
                expected_path = _resolve_file(self.root, case_ref.expected)
                fixture_bytes = fixture_path.read_bytes()
                expected_bytes = expected_path.read_bytes()
                fixture_input = _load_json(fixture_path)
                if not isinstance(fixture_input, dict):
                    raise BenchmarkCatalogError(
                        f"Benchmark input must be an object: {case_ref.fixture}"
                    )
                digest = hashlib.sha256(
                    fixture_bytes + b"\0" + expected_bytes
                ).hexdigest()
                cases.append(
                    LoadedBenchmarkCase(
                        case_id=case_ref.case_id,
                        fixture_path=case_ref.fixture,
                        expected_path=case_ref.expected,
                        fixture_digest=f"sha256:{digest}",
                        input=fixture_input,
                        expected=_load_json(expected_path),
                    )
                )
            loaded_profiles.append(
                LoadedBenchmarkProfile(
                    profile=profile, cases=tuple(cases), source_path=source_path
                )
            )
        if not loaded_profiles:
            raise BenchmarkCatalogError(f"No benchmark profiles found in {profile_dir}")
        return tuple(loaded_profiles)
