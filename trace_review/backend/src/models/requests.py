"""
Request models for API endpoints
"""
from pydantic import BaseModel, Field


class AnalyzeTraceRequest(BaseModel):
    """Request to analyze a trace"""
    trace_id: str = Field(..., description="Langfuse trace ID (32-char hex)")
    source: str = Field(default="remote", description="Trace source: 'remote' (EC2) or 'local' (Docker)")


class DevBypassRequest(BaseModel):
    """Request for dev mode authentication bypass"""
    dev_key: str = Field(default="dev", description="Dev authentication key")
