"""Manual live smoke test for real PDFX service integration."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest


@pytest.mark.integration
@pytest.mark.live_pdfx
@pytest.mark.manual_only
def test_live_pdfx_health_and_extract_smoke():
    if os.getenv("PDFX_LIVE_ENABLE", "").strip() != "1":
        pytest.skip("Set PDFX_LIVE_ENABLE=1 to run manual live PDFX smoke test")

    base_url = os.getenv("PDFX_LIVE_BASE_URL", "").rstrip("/")
    sample_pdf = os.getenv("PDFX_LIVE_SAMPLE_PDF", "").strip()
    bearer_token = os.getenv("PDFX_LIVE_BEARER_TOKEN", "").strip()

    if not base_url:
        pytest.skip("Set PDFX_LIVE_BASE_URL to run live PDFX smoke test")
    if not sample_pdf:
        pytest.skip("Set PDFX_LIVE_SAMPLE_PDF to run extraction smoke test")

    sample_path = Path(sample_pdf)
    if not sample_path.exists():
        pytest.skip(f"Sample PDF not found: {sample_path}")

    timeout_seconds = int(os.getenv("PDFX_LIVE_TIMEOUT_SECONDS", "300"))
    poll_interval_seconds = float(os.getenv("PDFX_LIVE_POLL_INTERVAL_SECONDS", "2"))
    methods = os.getenv("PDFX_LIVE_METHODS", "grobid,docling,marker")

    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{base_url}/api/v1/health", headers=headers or None)
        assert health.status_code == 200, f"/health returned {health.status_code}"

        deep_health = client.get(f"{base_url}/api/v1/health/deep", headers=headers or None)
        if bearer_token:
            assert deep_health.status_code == 200, f"/health/deep returned {deep_health.status_code}"
        else:
            assert deep_health.status_code in {200, 401, 403}, (
                f"/health/deep returned unexpected status {deep_health.status_code}"
            )

        with sample_path.open("rb") as file_handle:
            files = {"file": (sample_path.name, file_handle, "application/pdf")}
            data = {
                "methods": methods,
                "merge": "true",
            }
            submit = client.post(f"{base_url}/api/v1/extract", files=files, data=data, headers=headers or None)

        assert submit.status_code == 202, f"/extract returned {submit.status_code}"
        payload = submit.json()
        process_id = str(payload.get("process_id", "")).strip()
        assert process_id, payload

        status = None
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status_resp = client.get(
                f"{base_url}/api/v1/extract/{process_id}/status",
                headers=headers or None,
            )
            assert status_resp.status_code < 500, f"/status returned {status_resp.status_code}"
            status_payload = status_resp.json() if status_resp.content else {}
            status = str(status_payload.get("status", "")).lower()
            if status in {"complete", "failed"}:
                break
            time.sleep(poll_interval_seconds)

        assert status == "complete", f"Final status was '{status}' for process_id={process_id}"

        download = client.get(
            f"{base_url}/api/v1/extract/{process_id}/download/merged",
            headers=headers or None,
        )
        assert download.status_code == 200, f"/download/merged returned {download.status_code}"
        markdown = download.text.strip()
        assert len(markdown) >= 20
        assert any(char.isalpha() for char in markdown)
