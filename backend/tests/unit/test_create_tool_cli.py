"""Tests for create_tool CLI tool."""
import pytest
import sys
from pathlib import Path
from io import StringIO
from unittest.mock import patch, MagicMock

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))

from create_tool import (
    validate_tool_id,
    validate_param_type,
    check_tool_exists,
    parse_params,
    generate_result_model,
    generate_tool_function,
    generate_tool_file,
    generate_tool_override_entry,
    print_preview,
    confirm_proceed,
    NewToolInput,
    ToolParam,
)


class TestValidateToolId:
    """Tests for validate_tool_id function."""

    def test_accepts_valid_snake_case(self):
        """Valid snake_case tool IDs should pass."""
        validate_tool_id("my_tool")  # Should not raise
        validate_tool_id("gene_expression_query")
        validate_tool_id("a")
        validate_tool_id("tool123")
        validate_tool_id("tool_123_abc")

    def test_rejects_uppercase(self):
        """Tool IDs with uppercase should be rejected."""
        with pytest.raises(ValueError):
            validate_tool_id("MyTool")

    def test_rejects_hyphens(self):
        """Tool IDs with hyphens should be rejected."""
        with pytest.raises(ValueError):
            validate_tool_id("my-tool")

    def test_rejects_leading_digit(self):
        """Tool IDs starting with digit should be rejected."""
        with pytest.raises(ValueError):
            validate_tool_id("123tool")

    def test_rejects_spaces(self):
        """Tool IDs with spaces should be rejected."""
        with pytest.raises(ValueError):
            validate_tool_id("my tool")


class TestValidateParamType:
    """Tests for validate_param_type function."""

    def test_accepts_common_types(self):
        """Common Python types should return no warnings."""
        assert validate_param_type("str") == []
        assert validate_param_type("int") == []
        assert validate_param_type("bool") == []
        assert validate_param_type("float") == []
        assert validate_param_type("List[str]") == []
        assert validate_param_type("Dict[str, Any]") == []
        assert validate_param_type("Optional[int]") == []

    def test_warns_on_common_typos(self):
        """Common typos like 'string' should produce warnings."""
        warnings = validate_param_type("string")
        assert len(warnings) > 0
        assert "typo" in warnings[0].lower()

        warnings = validate_param_type("integer")
        assert len(warnings) > 0

        warnings = validate_param_type("boolean")
        assert len(warnings) > 0

        warnings = validate_param_type("strin")
        assert len(warnings) > 0

    def test_warns_on_unknown_types(self):
        """Unknown lowercase types should produce warnings."""
        warnings = validate_param_type("mytype")
        assert len(warnings) > 0
        assert "not a common Python type" in warnings[0]

    def test_accepts_custom_class_types(self):
        """Custom class types (starting uppercase) should not warn."""
        warnings = validate_param_type("MyCustomClass")
        assert len(warnings) == 0


class TestCheckToolExists:
    """Tests for check_tool_exists function."""

    def test_returns_false_for_nonexistent(self):
        """Nonexistent tool should return False."""
        assert check_tool_exists("totally_fake_tool_xyz_12345") is False

    def test_returns_true_for_existing(self):
        """Existing tool files should return True."""
        # agr_curation.py exists in tools directory
        assert check_tool_exists("agr_curation") is True


class TestParseParams:
    """Tests for parse_params function."""

    def test_parses_simple_params(self):
        """Simple name:type params should parse correctly."""
        params = parse_params("query:str,limit:int")
        assert len(params) == 2
        assert params[0].name == "query"
        assert params[0].param_type == "str"
        assert params[0].default is None
        assert params[1].name == "limit"
        assert params[1].param_type == "int"

    def test_parses_params_with_defaults(self):
        """Params with defaults should parse correctly."""
        params = parse_params("query:str,limit:int=10,active:bool=True")
        assert len(params) == 3
        assert params[1].default == "10"
        assert params[2].default == "True"

    def test_handles_empty_string(self):
        """Empty string should return empty list."""
        assert parse_params("") == []
        assert parse_params("   ") == []

    def test_handles_spaces(self):
        """Spaces around params should be handled."""
        params = parse_params("  query : str , limit : int = 10  ")
        assert len(params) == 2
        assert params[0].name == "query"
        assert params[0].param_type == "str"
        assert params[1].default == "10"

    def test_rejects_missing_type(self):
        """Params without type should raise ValueError."""
        with pytest.raises(ValueError):
            parse_params("query,limit")

    def test_handles_complex_types(self):
        """Complex generic types should parse correctly."""
        params = parse_params("items:List[str],mapping:Dict[str, Any]")
        assert params[0].param_type == "List[str]"
        assert params[1].param_type == "Dict[str, Any]"


class TestToolParamClass:
    """Tests for ToolParam dataclass."""

    def test_to_signature_without_default(self):
        """Signature without default should be name: type."""
        param = ToolParam(name="query", param_type="str")
        assert param.to_signature() == "query: str"

    def test_to_signature_with_default(self):
        """Signature with default should include default value."""
        param = ToolParam(name="limit", param_type="int", default="10")
        assert param.to_signature() == "limit: int = 10"

    def test_to_docstring(self):
        """Docstring should include parameter name."""
        param = ToolParam(name="query", param_type="str", description="Search query")
        docstring = param.to_docstring()
        assert "query" in docstring
        assert "Search query" in docstring


class TestGenerateResultModel:
    """Tests for generate_result_model function."""

    def test_generates_valid_pydantic_model(self):
        """Should generate valid Pydantic model code."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
        )
        code = generate_result_model(config)

        assert "class MyToolResult(BaseModel):" in code
        assert "status: str" in code
        assert "data: Any" in code
        assert "message: Optional[str]" in code


class TestGenerateToolFunction:
    """Tests for generate_tool_function function."""

    def test_generates_async_function(self):
        """Should generate async function by default."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
            is_async=True,
        )
        code = generate_tool_function(config)

        assert "@function_tool" in code
        assert "async def my_tool(" in code
        assert "-> MyToolResult:" in code

    def test_generates_sync_function(self):
        """Should generate sync function when is_async=False."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
            is_async=False,
        )
        code = generate_tool_function(config)

        assert "@function_tool" in code
        assert "async def my_tool" not in code
        assert "def my_tool(" in code

    def test_includes_parameters(self):
        """Should include parameters in function signature."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
            params=[
                ToolParam(name="query", param_type="str"),
                ToolParam(name="limit", param_type="int", default="10"),
            ],
        )
        code = generate_tool_function(config)

        assert "query: str" in code
        assert "limit: int = 10" in code

    def test_includes_langfuse_note(self):
        """Should include Langfuse tracing integration note."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
        )
        code = generate_tool_function(config)

        assert "Langfuse" in code or "langfuse" in code

    def test_includes_error_handling(self):
        """Should include structured error handling."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
        )
        code = generate_tool_function(config)

        assert "except ValueError as e:" in code
        assert "except Exception as e:" in code
        assert "exc_info=True" in code


class TestGenerateToolFile:
    """Tests for generate_tool_file function."""

    def test_generates_complete_module(self):
        """Should generate a complete Python module."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
        )
        code = generate_tool_file(config)

        # Module docstring
        assert '"""' in code
        assert "My Tool" in code

        # Imports
        assert "import logging" in code
        assert "from pydantic import BaseModel" in code
        assert "from agents import function_tool" in code

        # Logger
        assert "logger = logging.getLogger(__name__)" in code

        # Result model
        assert "class MyToolResult(BaseModel):" in code

        # Function
        assert "@function_tool" in code
        assert "def my_tool(" in code


class TestGenerateToolOverrideEntry:
    """Tests for generate_tool_override_entry function."""

    def test_generates_valid_entry(self):
        """Should generate valid TOOL_OVERRIDES entry."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
            category="API",
        )
        entry = generate_tool_override_entry(config)

        assert '"my_tool"' in entry
        assert '"category": "API"' in entry
        assert '"description": "A test tool"' in entry


class TestPrintPreview:
    """Tests for print_preview function."""

    def test_prints_preview_information(self, capsys):
        """Should print preview with all relevant information."""
        config = NewToolInput(
            tool_id="my_tool",
            name="My Tool",
            description="A test tool",
            return_type="MyToolResult",
            params=[ToolParam(name="query", param_type="str")],
            category="API",
        )

        print_preview(config)
        captured = capsys.readouterr()

        # Check header
        assert "TOOL CREATION PREVIEW" in captured.out

        # Check config details
        assert "my_tool" in captured.out
        assert "My Tool" in captured.out
        assert "MyToolResult" in captured.out
        assert "query" in captured.out

        # Check file information
        assert "CREATE" in captured.out or "OVERWRITE" in captured.out
        assert "__init__.py" in captured.out

        # Check next steps
        assert "AFTER CREATION" in captured.out


class TestConfirmProceed:
    """Tests for confirm_proceed function."""

    def test_yes_flag_skips_confirmation(self):
        """--yes flag should skip confirmation."""
        args = MagicMock()
        args.yes = True

        assert confirm_proceed(args) is True

    def test_y_input_returns_true(self):
        """Input 'y' should return True."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='y'):
            assert confirm_proceed(args) is True

    def test_yes_input_returns_true(self):
        """Input 'yes' should return True."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='yes'):
            assert confirm_proceed(args) is True

    def test_n_input_returns_false(self):
        """Input 'n' should return False."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='n'):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False

    def test_p_input_returns_none(self):
        """Input 'p' should return None (preview)."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='p'):
            assert confirm_proceed(args) is None

    def test_eof_returns_false(self):
        """EOFError should return False."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', side_effect=EOFError):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False

    def test_keyboard_interrupt_returns_false(self):
        """KeyboardInterrupt should return False."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', side_effect=KeyboardInterrupt):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False
