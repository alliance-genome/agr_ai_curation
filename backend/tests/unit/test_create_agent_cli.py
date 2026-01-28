"""Tests for create_agent CLI tool."""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))

from create_agent import (
    validate_agent_id,
    validate_category,
    validate_icon,
    generate_agent_skeleton,
    generate_registry_entry,
    generate_default_prompt,
    print_preview,
    confirm_proceed,
    NewAgentInput,
)


class TestValidateAgentId:
    """Tests for validate_agent_id function."""

    def test_accepts_valid_snake_case(self):
        """Valid agent IDs should pass."""
        validate_agent_id("gene_expression")  # Should not raise
        validate_agent_id("my_agent")
        validate_agent_id("a")
        validate_agent_id("agent123")

    def test_rejects_hyphens(self):
        """Agent IDs with hyphens should be rejected."""
        with pytest.raises(ValueError):
            validate_agent_id("Agent-Name")

    def test_rejects_leading_digits(self):
        """Agent IDs starting with digits should be rejected."""
        with pytest.raises(ValueError):
            validate_agent_id("123agent")

    def test_rejects_uppercase(self):
        """Agent IDs with uppercase should be rejected."""
        with pytest.raises(ValueError):
            validate_agent_id("MyAgent")

    def test_rejects_spaces(self):
        """Agent IDs with spaces should be rejected."""
        with pytest.raises(ValueError):
            validate_agent_id("my agent")


class TestValidateCategory:
    """Tests for validate_category function."""

    def test_accepts_valid_categories(self):
        """Valid categories should pass."""
        validate_category("Validation")
        validate_category("Extraction")
        validate_category("Output")

    def test_rejects_invalid_categories(self):
        """Invalid categories should raise."""
        with pytest.raises(ValueError):
            validate_category("InvalidCategory")


class TestValidateIcon:
    """Tests for validate_icon function."""

    def test_accepts_single_emoji(self):
        """Single emoji should pass."""
        validate_icon("🧪")  # Should not raise
        validate_icon("📊")
        validate_icon("🔍")

    def test_rejects_long_string(self):
        """Long strings should be rejected."""
        with pytest.raises(ValueError):
            validate_icon("not an emoji")


class TestGenerateAgentSkeleton:
    """Tests for generate_agent_skeleton function."""

    def test_creates_valid_code(self):
        """Should generate valid Python code."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
        )
        code = generate_agent_skeleton(config)
        assert "def create_test_agent_agent" in code
        assert "Test Agent" in code

    def test_uses_agent_config_variable_name(self):
        """Should use agent_config not config to avoid shadowing."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
        )
        code = generate_agent_skeleton(config)
        # Should use agent_config, not config, for the config variable
        assert "agent_config = get_agent_config" in code

    def test_includes_mod_rules_injection(self):
        """Should include MOD rules injection code."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
        )
        code = generate_agent_skeleton(config)
        assert "inject_mod_rules" in code
        assert "active_mods" in code

    def test_includes_output_type_comment(self):
        """Should include output_type placeholder comment."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
        )
        code = generate_agent_skeleton(config)
        assert "output_type" in code

    def test_includes_document_params_when_required(self):
        """Should include document params when requires_document=True."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
            requires_document=True,
        )
        code = generate_agent_skeleton(config)
        assert "document_id" in code
        assert "user_id" in code


class TestGenerateRegistryEntry:
    """Tests for generate_registry_entry function."""

    def test_creates_valid_dict(self):
        """Should generate registry entry dict."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
            icon="🧪",
        )
        entry = generate_registry_entry(config)
        assert entry["name"] == "Test Agent"
        assert entry["frontend"]["icon"] == "🧪"
        assert entry["category"] == "Validation"

    def test_includes_supervisor_config(self):
        """Should include supervisor configuration."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
        )
        entry = generate_registry_entry(config)
        assert "supervisor" in entry
        assert entry["supervisor"]["enabled"] is True
        assert entry["supervisor"]["tool_name"] == "ask_test_agent_specialist"

    def test_includes_batch_capabilities_when_requires_document(self):
        """Should include batch_capabilities when requires_document=True."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
            requires_document=True,
        )
        entry = generate_registry_entry(config)
        assert "pdf_extraction" in entry["batch_capabilities"]


class TestGenerateDefaultPrompt:
    """Tests for generate_default_prompt function."""

    def test_creates_prompt_with_tools(self):
        """Should generate prompt listing tools."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["tool_one", "tool_two"],
        )
        prompt = generate_default_prompt(config)
        assert "Test Agent" in prompt
        assert "tool_one" in prompt
        assert "tool_two" in prompt


class TestPrintPreview:
    """Tests for print_preview function."""

    def test_prints_preview_information(self, capsys):
        """Should print preview with all relevant information."""
        config = NewAgentInput(
            agent_id="test_agent",
            name="Test Agent",
            description="A test agent",
            category="Validation",
            tools=["test_tool"],
            icon="🧪",
        )
        entry = generate_registry_entry(config)

        print_preview(config, entry)
        captured = capsys.readouterr()

        # Check header
        assert "AGENT CREATION PREVIEW" in captured.out

        # Check config details
        assert "test_agent" in captured.out
        assert "Test Agent" in captured.out
        assert "Validation" in captured.out

        # Check file information
        assert "CREATE" in captured.out
        assert "__init__.py" in captured.out
        assert "catalog_service.py" in captured.out

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

    def test_no_input_returns_false(self):
        """Input 'no' should return False."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='no'):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False

    def test_p_input_returns_none(self):
        """Input 'p' should return None (signal for preview)."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='p'):
            assert confirm_proceed(args) is None

    def test_preview_input_returns_none(self):
        """Input 'preview' should return None."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', return_value='preview'):
            assert confirm_proceed(args) is None

    def test_eof_returns_false(self):
        """EOFError should return False (cancelled)."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', side_effect=EOFError):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False

    def test_keyboard_interrupt_returns_false(self):
        """KeyboardInterrupt should return False (cancelled)."""
        args = MagicMock()
        args.yes = False

        with patch('builtins.input', side_effect=KeyboardInterrupt):
            with patch('builtins.print'):
                assert confirm_proceed(args) is False
