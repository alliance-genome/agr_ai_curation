"""Manual live smoke tests for ABC Literature read-only import contracts."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest


DEFAULT_STAGE_BASE_URL = "https://stage-literature-rest.alliancegenome.org"
ALLOWED_ENDPOINTS = {
    "/reference/referencefile/by_md5/{md5sum}": "get",
    "/reference/external_lookup/{external_curie}": "get",
    "/reference/by_cross_reference/{curie_or_cross_reference_id}": "get",
    "/reference/{curie_or_reference_id}": "get",
    "/search/references/": "post",
    "/reference/referencefile/show_all/{curie_or_reference_id}": "get",
    "/reference/referencefile/download_file/{referencefile_id}": "get",
}
FORBIDDEN_ENDPOINTS = {
    "/reference/add/": "post",
    "/reference/referencefile/file_upload/": "post",
    "/reference/referencefile/conversion_request/{curie_or_reference_id}": "get",
}


def _live_enabled() -> None:
    if os.getenv("ABC_LITERATURE_LIVE_ENABLE", "").strip() != "1":
        pytest.skip(
            "Set ABC_LITERATURE_LIVE_ENABLE=1 to run manual ABC Literature smoke tests"
        )


def _base_url() -> str:
    return os.getenv("ABC_LITERATURE_LIVE_BASE_URL", DEFAULT_STAGE_BASE_URL).rstrip("/")


def _timeout_seconds() -> float:
    return float(os.getenv("ABC_LITERATURE_LIVE_TIMEOUT_SECONDS", "20"))


def _bearer_headers(*, required: bool = False) -> dict[str, str]:
    token = os.getenv("ABC_LITERATURE_LIVE_BEARER_TOKEN", "").strip()
    if required and not token:
        pytest.skip("Set ABC_LITERATURE_LIVE_BEARER_TOKEN for authenticated live calls")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _required_env(name: str, purpose: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"Set {name} to {purpose}")
    return value


def _get_openapi(client: httpx.Client) -> dict[str, Any]:
    response = client.get(f"{_base_url()}/openapi.json")
    assert response.status_code == 200, f"openapi.json returned {response.status_code}"
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _path_operation(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    paths = schema.get("paths")
    assert isinstance(paths, dict), "OpenAPI schema missing paths"
    path_item = paths.get(path)
    assert isinstance(path_item, dict), f"OpenAPI schema missing {path}"
    operation = path_item.get(method)
    assert isinstance(operation, dict), f"OpenAPI schema missing {method.upper()} {path}"
    return operation


def _get_json(client: httpx.Client, path: str, *, headers: dict[str, str]) -> Any:
    response = client.get(f"{_base_url()}{path}", headers=headers or None)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    return response.json()


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_openapi_read_only_contract() -> None:
    _live_enabled()

    with httpx.Client(timeout=_timeout_seconds()) as client:
        schema = _get_openapi(client)

    assert schema.get("openapi") == "3.1.0"
    assert (schema.get("info") or {}).get("title") == "Alliance Literature Service"

    for path, method in ALLOWED_ENDPOINTS.items():
        operation = _path_operation(schema, path, method)
        security = operation.get("security") or []
        assert {"HTTPBearer": []} in security, f"{method.upper()} {path} is not bearer-protected"

    conversion_request = _path_operation(
        schema,
        "/reference/referencefile/conversion_request/{curie_or_reference_id}",
        "get",
    )
    parameter_names = {
        parameter.get("name")
        for parameter in conversion_request.get("parameters", [])
        if isinstance(parameter, dict)
    }
    assert {"wait", "overwrite_tei_md"}.issubset(parameter_names)

    for path, method in FORBIDDEN_ENDPOINTS.items():
        _path_operation(schema, path, method)


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_checksum_lookup_fixtures() -> None:
    _live_enabled()
    headers = _bearer_headers(required=True)
    unknown_md5 = os.getenv(
        "ABC_LITERATURE_LIVE_UNKNOWN_MD5",
        "0" * 32,
    ).strip()
    known_md5 = _required_env(
        "ABC_LITERATURE_LIVE_KNOWN_MD5",
        "a safe checksum fixture with a source row and converted Markdown",
    )

    with httpx.Client(timeout=_timeout_seconds()) as client:
        unknown_payload = _get_json(
            client,
            f"/reference/referencefile/by_md5/{unknown_md5}",
            headers=headers,
        )
        assert unknown_payload == []

        known_payload = _get_json(
            client,
            f"/reference/referencefile/by_md5/{known_md5}",
            headers=headers,
        )

    assert isinstance(known_payload, list)
    assert known_payload, "Known MD5 fixture returned no matches"
    source = known_payload[0]
    assert isinstance(source, dict)
    for key in (
        "referencefile_id",
        "reference_id",
        "reference_curie",
        "referencefile_mods",
        "converted_referencefiles",
    ):
        assert key in source
    converted = source.get("converted_referencefiles")
    assert isinstance(converted, list)
    assert any(
        str(item.get("file_extension", "")).lower() in {"md", "markdown"}
        for item in converted
        if isinstance(item, dict)
    ), "Known MD5 fixture did not include converted Markdown"


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_restricted_checksum_fixture() -> None:
    _live_enabled()
    headers = _bearer_headers(required=True)
    restricted_md5 = _required_env(
        "ABC_LITERATURE_LIVE_RESTRICTED_MD5",
        "a safe checksum fixture whose source PDF has restricted MOD metadata",
    )

    with httpx.Client(timeout=_timeout_seconds()) as client:
        payload = _get_json(
            client,
            f"/reference/referencefile/by_md5/{restricted_md5}",
            headers=headers,
        )

    assert isinstance(payload, list)
    assert payload, "Restricted MD5 fixture returned no matches"
    source = payload[0]
    assert isinstance(source, dict)
    mods = source.get("referencefile_mods")
    assert isinstance(mods, list)
    assert any(
        isinstance(item, dict) and str(item.get("mod_abbreviation") or "").strip()
        for item in mods
    ), "Restricted MD5 fixture did not include source-PDF MOD metadata"


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_reference_lookup_and_show_all_fixtures() -> None:
    _live_enabled()
    headers = _bearer_headers(required=True)
    pmid = _required_env(
        "ABC_LITERATURE_LIVE_PMID",
        "a safe PMID fixture, e.g. PMID:12345 or 12345",
    )
    reference = _required_env(
        "ABC_LITERATURE_LIVE_REFERENCE",
        "a safe AGRKB/reference curie or reference id fixture",
    )

    external_curie = pmid if ":" in pmid else f"PMID:{pmid}"
    with httpx.Client(timeout=_timeout_seconds()) as client:
        lookup = _get_json(
            client,
            f"/reference/external_lookup/{external_curie}",
            headers=headers,
        )
        native_reference = _get_json(client, f"/reference/{reference}", headers=headers)
        files = _get_json(
            client,
            f"/reference/referencefile/show_all/{reference}",
            headers=headers,
        )

    assert isinstance(lookup, dict)
    assert lookup.get("external_curie_found") is True
    assert isinstance(native_reference, dict)
    assert native_reference.get("curie") or native_reference.get("reference_id")
    assert isinstance(files, list)
    assert any(
        str(item.get("file_extension", "")).lower() in {"md", "markdown"}
        for item in files
        if isinstance(item, dict)
    ), "show_all fixture did not include Markdown"


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_download_file_fixture() -> None:
    _live_enabled()
    headers = _bearer_headers(required=True)
    referencefile_id = _required_env(
        "ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID",
        "a safe converted Markdown referencefile id fixture",
    )

    with httpx.Client(timeout=_timeout_seconds()) as client:
        response = client.get(
            f"{_base_url()}/reference/referencefile/download_file/{referencefile_id}",
            headers=headers,
        )

    assert response.status_code == 200, f"download_file returned {response.status_code}"
    content_type = response.headers.get("content-type", "")
    assert content_type, "download_file response did not include a content-type"
    text = response.content.decode("utf-8", errors="replace").strip()
    assert len(text) >= 20
    assert any(char.isalpha() for char in text)


@pytest.mark.integration
@pytest.mark.live_literature
@pytest.mark.manual_only
def test_live_literature_download_file_unauthorized_fixture() -> None:
    _live_enabled()
    unauthorized_token = _required_env(
        "ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN",
        "a bearer credential expected to receive 403 for the restricted fixture",
    )
    referencefile_id = _required_env(
        "ABC_LITERATURE_LIVE_RESTRICTED_REFERENCEFILE_ID",
        "a restricted converted Markdown referencefile id fixture",
    )

    with httpx.Client(timeout=_timeout_seconds()) as client:
        response = client.get(
            f"{_base_url()}/reference/referencefile/download_file/{referencefile_id}",
            headers={"Authorization": f"Bearer {unauthorized_token}"},
        )

    assert response.status_code == 403, (
        "download_file unauthorized fixture must return authoritative 403; "
        f"got {response.status_code}"
    )
