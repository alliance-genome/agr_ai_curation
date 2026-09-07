"""Cold-process proof using real SQL prompts and the unchanged agent builder.

Opt in with BENCHMARK_REPLACEMENT_CANARY=true in the disposable canary
database. This tests construction, not provider execution or extraction quality.
"""

import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from src.models.sql.agent import Agent
from src.models.sql.database import SessionLocal
from src.models.sql.prompts import PromptTemplate


pytestmark = pytest.mark.skipif(
    os.getenv("BENCHMARK_REPLACEMENT_CANARY") != "true",
    reason="Requires the disposable benchmark canary database",
)


def test_cold_worker_builds_real_agent():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "postgres-test" and url.database == "benchmark_replacement_canary"
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    with SessionLocal() as db:
        # Migrations seed the shipped agent and prompt. Keep their real identity
        # and restore construction-only fixture changes afterwards.
        agent = db.scalar(select(Agent).where(Agent.agent_key == "supervisor"))
        assert agent is not None
        original_model, original_tools = agent.model_id, agent.tool_ids
        agent.model_id, agent.tool_ids = "gpt-5.6-terra", []
        prompt = db.scalars(select(PromptTemplate).where(
            PromptTemplate.agent_name == "supervisor",
            PromptTemplate.prompt_type == "system",
            PromptTemplate.group_id.is_(None),
            PromptTemplate.is_active.is_(True),
        )).one()
        original_content = prompt.content
        prompt.content = "Synthetic retained cold-worker prompt ALL-1110."
        db.commit()
        agent_id, prompt_id = agent.id, prompt.id
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tests.integration.persistence.benchmark_worker_startup_probe"],
            env={
                **os.environ,
                "BENCHMARK_WORKER_ENABLED": "true",
                "BENCHMARK_EXECUTION_ENABLED": "true",
                "BENCHMARK_WORKER_CONCURRENCY": "1",
                "OPENAI_API_KEY": "synthetic-no-provider-call",
                "OPENROUTER_API_KEY": "",
                "LANGFUSE_PUBLIC_KEY": "",
                "LANGFUSE_SECRET_KEY": "",
            },
            capture_output=True, text=True, check=False,
            timeout=float(os.getenv("BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS", "30")),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "COLD_WORKER_REAL_BUILDER_OK" in result.stdout
    finally:
        with SessionLocal() as db:
            prompt = db.get(PromptTemplate, prompt_id)
            prompt.content = original_content
            agent = db.get(Agent, agent_id)
            agent.model_id, agent.tool_ids = original_model, original_tools
            db.commit()
