"""OTLP span exporter for the configured Langfuse project."""

import base64
import os
from collections.abc import Sequence
from importlib.metadata import version

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult

from src.lib.observability.langfuse import own_langfuse_sdk_logs, report_langfuse_failure
from src.lib.openai_agents.config import get_langfuse_timeout


class LangfuseSpanExporter(OTLPSpanExporter):
    def __init__(self, host: str, public_key: str, secret_key: str):
        # Langfuse does not configure endpoint, auth or timeout for an exporter
        # supplied through its span_exporter constructor argument.
        base_url = os.getenv("LANGFUSE_BASE_URL") or host
        path = os.getenv("LANGFUSE_OTEL_TRACES_EXPORT_PATH") or "api/public/otel/v1/traces"
        credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
        super().__init__(
            endpoint=f"{base_url}/{path}",
            headers={
                "Authorization": f"Basic {credentials}",
                "x-langfuse-sdk-name": "python",
                "x-langfuse-sdk-version": version("langfuse"),
                "x-langfuse-public-key": public_key,
            },
            timeout=get_langfuse_timeout(),
        )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        # The batch processor discards the result, even during a force_flush.
        with own_langfuse_sdk_logs(OTLPSpanExporter.__module__):
            try:
                result = super().export(spans)
            except Exception as exc:
                report_langfuse_failure("export_failed", exc)
                return SpanExportResult.FAILURE
        if result is SpanExportResult.FAILURE:
            report_langfuse_failure("export_failed")
        return result
