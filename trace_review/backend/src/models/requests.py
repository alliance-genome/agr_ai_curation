"""
Request models for API endpoints
"""
from typing import Literal

from pydantic import BaseModel, Field


TraceSource = Literal["remote", "local"]


class AnalyzeTraceRequest(BaseModel):
    """Request to analyze a trace"""
    trace_id: str = Field(..., description="Langfuse trace ID (32-char hex)")
    source: TraceSource = Field(
        default="remote",
        description="Trace source: 'remote' (EC2) or 'local' (Docker)",
    )


class DevBypassRequest(BaseModel):
    """Request for dev mode authentication bypass"""
    dev_key: str = Field(default="dev", description="Dev authentication key")
