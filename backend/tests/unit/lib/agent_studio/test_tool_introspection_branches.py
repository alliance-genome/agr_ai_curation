"""Additional branch tests for tool introspection utility."""

from types import SimpleNamespace

import pytest

from src.lib.agent_studio import tool_introspection as introspection


def test_introspect_function_tool_object_reads_schema_fields():
    tool = SimpleNamespace(
        name="search_docs",
        description="Search indexed docs",
        params_json_schema={
            "properties": {
                "query": {"type": "string", "description": "Search text"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    )

    metadata = introspection.introspect_tool(tool)
    assert metadata.name == "search_docs"
    assert metadata.description == "Search indexed docs"
    assert metadata.parameters["query"] == {
        "type": "string",
        "required": True,
        "description": "Search text",
    }
    assert metadata.parameters["limit"] == {
        "type": "integer",
        "required": False,
        "default": 5,
    }
    assert metadata.return_type is None


def test_introspect_raw_function_extracts_name_doc_params_and_return_type():
    def raw_tool(query: str, limit: int = 10) -> bool:
        """Check whether query is valid.

        Additional details that should not be included.
        """
        return bool(query) and limit > 0

    metadata = introspection.introspect_tool(raw_tool)
    assert metadata.name == "raw_tool"
    assert metadata.description == "Check whether query is valid."
    assert metadata.parameters["query"]["type"] == "string"
    assert metadata.parameters["query"]["required"] is True
    assert metadata.parameters["limit"]["type"] == "integer"
    assert metadata.parameters["limit"]["required"] is False
    assert metadata.parameters["limit"]["default"] == 10
    assert metadata.return_type == "boolean"
    assert metadata.source_file is not None


def test_introspect_raw_function_prefers_explicit_name_and_skips_self():
    class Demo:
        def method(self, value: str) -> str:
            return value

    def wrapper(*args, **kwargs):
        return Demo().method(*args, **kwargs)

    wrapper.__wrapped__ = Demo.method
    wrapper.name = "custom_tool_name"

    metadata = introspection.introspect_tool(wrapper)
    assert metadata.name == "custom_tool_name"
    assert "self" not in metadata.parameters
    assert "value" in metadata.parameters


def test_introspect_raw_function_tolerates_signature_errors(monkeypatch):
    def sample_tool(a: int) -> int:
        return a

    monkeypatch.setattr(
        introspection.inspect,
        "signature",
        lambda _func: (_ for _ in ()).throw(TypeError("no signature")),
    )

    metadata = introspection._introspect_raw_function(sample_tool)
    assert metadata.parameters == {}
    assert metadata.return_type == "integer"


def test_safe_get_type_hints_falls_back_to_annotations_when_resolution_fails(monkeypatch):
    def sample():
        return None

    sample.__annotations__ = {
        "a": "str",
        "b": "int",
        "c": "CustomThing",
        "return": "bool",
    }

    monkeypatch.setattr(
        introspection,
        "get_type_hints",
        lambda _func: (_ for _ in ()).throw(NameError("missing name")),
    )

    hints = introspection._safe_get_type_hints(sample)
    assert hints["a"] is str
    assert hints["b"] is int
    assert hints["c"] == "CustomThing"
    assert hints["return"] is bool


@pytest.mark.parametrize(
    ("py_type", "expected"),
    [
        (None, "any"),
        (str, "string"),
        (int, "integer"),
        (float, "number"),
        (bool, "boolean"),
        (list, "array"),
        (dict, "object"),
        (list[str], "array"),
        (dict[str, int], "object"),
        (set, "string"),
    ],
)
def test_python_type_to_json_type_mappings(py_type, expected):
    assert introspection._python_type_to_json_type(py_type) == expected
