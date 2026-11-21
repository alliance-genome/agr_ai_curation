"""What backend startup creates in the search index must not change by accident.

The fixture holds the create payloads (as the Weaviate client sends them) that
``main.initialize_weaviate_collections`` produced before the collection
definitions moved to ``src.lib.weaviate_client.collection_definitions``. The
test renders today's payloads the same way and compares them.

Regenerate the fixture only for a deliberate schema change:
``python -m tests.unit.lib.test_weaviate_collection_definitions``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from weaviate.collections.classes.config import _CollectionConfigCreate

FIXTURE = Path(__file__).parent / "fixtures" / "weaviate_collection_create_payloads.json"
EMBEDDING_MODEL = "text-embedding-3-small"


def render_startup_create_payloads() -> dict:
    """Run startup collection creation against an empty fake server; render each create call."""
    client = MagicMock()
    client.collections.list_all.return_value = []

    @contextmanager
    def session():
        yield client

    connection = MagicMock()
    connection.session.side_effect = session
    main = importlib.import_module("main")
    with patch.dict(
        "src.lib.weaviate_client.settings._current_config",
        {"embedding": {"modelName": EMBEDDING_MODEL}},
    ):
        asyncio.run(main.initialize_weaviate_collections(connection))
    payloads = {}
    for call in client.collections.create.call_args_list:
        kwargs = dict(call.kwargs)
        name = kwargs.pop("name")
        payloads[name] = _CollectionConfigCreate(name=name, **kwargs)._to_dict(
            emit_default_vector_index_type=True
        )
    return payloads


def test_startup_creates_the_same_collections_as_before():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert render_startup_create_payloads() == expected


if __name__ == "__main__":
    FIXTURE.write_text(
        json.dumps(render_startup_create_payloads(), indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
