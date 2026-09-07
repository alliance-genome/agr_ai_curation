"""Fresh-interpreter probe; invoked only by the disposable SQL regression."""

import asyncio

from src.lib.agent_studio.catalog_service import get_agent_by_id
from src.lib.benchmarks import worker


async def build_once(self):
    # The loop is finite, but startup, SQL lookup, prompt assembly and SDK agent
    # construction are real. No Runner/provider invocation occurs.
    agent = get_agent_by_id("supervisor")
    assert "Synthetic retained cold-worker prompt ALL-1110." in agent.instructions


def main():
    try:
        get_agent_by_id("supervisor")
    except RuntimeError as exc:
        assert "Prompt cache not initialized" in str(exc), str(exc)
    else:
        raise AssertionError("The probe unexpectedly inherited an initialized prompt cache")
    worker.BenchmarkWorker.run_forever = build_once
    asyncio.run(worker._main())
    print("COLD_WORKER_REAL_BUILDER_OK")


if __name__ == "__main__":
    main()
