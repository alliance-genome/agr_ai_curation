"""Unit tests for configuration and environment variable handling."""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestConfiguration:
    """Test configuration loading and validation."""

    def test_default_pdf_extraction_strategy(self):
        """Test default PDF extraction strategy when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            from src.config import get_extraction_strategy

            strategy = get_extraction_strategy()
            assert strategy == "fast"

    def test_custom_pdf_extraction_strategy(self):
        """Test custom PDF extraction strategy from environment."""
        with patch.dict(os.environ, {'PDF_EXTRACTION_STRATEGY': 'hi_res'}):
            from src.config import get_extraction_strategy

            strategy = get_extraction_strategy()
            assert strategy == "hi_res"

    def test_invalid_pdf_extraction_strategy(self):
        """Test validation of invalid extraction strategy."""
        with patch.dict(os.environ, {'PDF_EXTRACTION_STRATEGY': 'invalid_strategy'}):
            from src.config import validate_extraction_strategy

            with pytest.raises(ValueError) as exc_info:
                validate_extraction_strategy("invalid_strategy")

            assert "Invalid extraction strategy" in str(exc_info.value)

    def test_table_extraction_enabled_via_env(self):
        """Test enabling table extraction via environment variable."""
        test_cases = [
            ('true', True),
            ('True', True),
            ('TRUE', True),
            ('1', False),  # Only 'true' string should enable
            ('false', False),
            ('False', False),
            ('', False)
        ]

        for env_value, expected in test_cases:
            with patch.dict(os.environ, {'ENABLE_TABLE_EXTRACTION': env_value}):
                from src.config import is_table_extraction_enabled

                enabled = is_table_extraction_enabled()
                assert enabled == expected, f"Failed for env value: {env_value}"

    def test_weaviate_configuration(self):
        """Test Weaviate connection configuration from environment."""
        test_config = {
            'WEAVIATE_HOST': 'test-weaviate',
            'WEAVIATE_PORT': '9090',
            'WEAVIATE_SCHEME': 'https'
        }

        with patch.dict(os.environ, test_config):
            from src.config import get_weaviate_url

            url = get_weaviate_url()
            assert url == "https://test-weaviate:9090"

    def test_weaviate_default_configuration(self):
        """Test Weaviate default configuration when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            from src.config import get_weaviate_url

            url = get_weaviate_url()
            # Should use defaults
            assert url == "http://weaviate:8080"

    def test_openai_api_key_configuration(self):
        """Test OpenAI API key configuration."""
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key-123'}):
            from src.config import get_openai_api_key

            key = get_openai_api_key()
            assert key == "test-key-123"

    def test_missing_openai_api_key(self):
        """Test handling of missing OpenAI API key."""
        with patch.dict(os.environ, {}, clear=True):
            from src.config import get_openai_api_key

            # Should return None or raise an error depending on implementation
            key = get_openai_api_key()
            assert key is None or key == ""

    def test_log_level_configuration(self):
        """Test log level configuration from environment."""
        test_cases = [
            ('DEBUG', 'DEBUG'),
            ('INFO', 'INFO'),
            ('WARNING', 'WARNING'),
            ('ERROR', 'ERROR'),
            ('invalid', 'INFO')  # Should default to INFO for invalid values
        ]

        for env_value, expected in test_cases:
            with patch.dict(os.environ, {'LOG_LEVEL': env_value}):
                from src.config import get_log_level

                level = get_log_level()
                assert level == expected

    def test_chunk_configuration(self):
        """Test chunk size and overlap configuration."""
        test_config = {
            'MAX_CHUNK_SIZE': '2000',
            'CHUNK_OVERLAP': '200'
        }

        with patch.dict(os.environ, test_config):
            from src.config import get_chunk_config

            config = get_chunk_config()
            assert config['max_size'] == 2000
            assert config['overlap'] == 200

    def test_default_chunk_configuration(self):
        """Test default chunk configuration when not specified."""
        with patch.dict(os.environ, {}, clear=True):
            from src.config import get_chunk_config

            config = get_chunk_config()
            # Should use defaults
            assert config['max_size'] == 1000
            assert config['overlap'] == 100

    def test_invalid_numeric_configuration(self):
        """Test handling of invalid numeric configuration values."""
        test_config = {
            'MAX_CHUNK_SIZE': 'not-a-number',
            'CHUNK_OVERLAP': 'also-not-a-number'
        }

        with patch.dict(os.environ, test_config):
            from src.config import get_chunk_config

            # Should fall back to defaults for invalid values
            config = get_chunk_config()
            assert config['max_size'] == 1000  # Default
            assert config['overlap'] == 100    # Default

    def test_log_failed_chunks_configuration(self):
        """Test configuration for logging failed chunk insertions."""
        test_cases = [
            ('true', True),
            ('false', False),
            ('', True),  # Should default to True for safety
            ('invalid', True)  # Should default to True for invalid values
        ]

        for env_value, expected in test_cases:
            with patch.dict(os.environ, {'LOG_FAILED_CHUNKS': env_value}):
                from src.config import should_log_failed_chunks

                should_log = should_log_failed_chunks()
                assert should_log == expected

    def test_environment_file_loading(self):
        """Test loading configuration from .env file."""
        # Create a mock .env file content
        env_content = """
        PDF_EXTRACTION_STRATEGY=auto
        ENABLE_TABLE_EXTRACTION=true
        WEAVIATE_HOST=production-weaviate
        WEAVIATE_PORT=8080
        LOG_LEVEL=DEBUG
        """

        from unittest.mock import mock_open

        with patch('builtins.open', mock_open(read_data=env_content)):
            with patch('pathlib.Path.exists', return_value=True):
                from src.config import load_env_file

                # Load the env file
                env_vars = load_env_file()

                # Verify loaded values
                assert env_vars.get('PDF_EXTRACTION_STRATEGY') == 'auto'
                assert env_vars.get('ENABLE_TABLE_EXTRACTION') == 'true'
                assert env_vars.get('WEAVIATE_HOST') == 'production-weaviate'
                assert env_vars.get('LOG_LEVEL') == 'DEBUG'

    def test_configuration_validation(self):
        """Test overall configuration validation."""
        valid_config = {
            'PDF_EXTRACTION_STRATEGY': 'fast',
            'ENABLE_TABLE_EXTRACTION': 'true',
            'WEAVIATE_HOST': 'weaviate',
            'WEAVIATE_PORT': '8080',
            'OPENAI_API_KEY': 'sk-test-key',
            'MAX_CHUNK_SIZE': '1500',
            'CHUNK_OVERLAP': '150'
        }

        with patch.dict(os.environ, valid_config):
            from src.config import validate_configuration

            # Should not raise any errors
            is_valid = validate_configuration()
            assert is_valid is True

    def test_missing_required_configuration(self):
        """Test detection of missing required configuration."""
        # Missing OPENAI_API_KEY which might be required
        incomplete_config = {
            'WEAVIATE_HOST': 'weaviate',
            'WEAVIATE_PORT': '8080'
        }

        with patch.dict(os.environ, incomplete_config, clear=True):
            from src.config import validate_configuration, ConfigurationError

            # Should raise error for missing required config
            with pytest.raises(ConfigurationError) as exc_info:
                validate_configuration(require_openai=True)

            assert "OPENAI_API_KEY" in str(exc_info.value)

    def test_configuration_with_docker_compose_override(self):
        """Test that Docker Compose environment variables override .env file."""
        # Simulate .env file values
        env_file_vars = {
            'WEAVIATE_HOST': 'localhost',
            'WEAVIATE_PORT': '8080'
        }

        # Simulate Docker Compose override
        docker_override = {
            'WEAVIATE_HOST': 'weaviate',  # Docker service name
            'WEAVIATE_PORT': '8080'
        }

        # Docker Compose values should take precedence
        with patch.dict(os.environ, docker_override):
            from src.config import get_weaviate_url

            url = get_weaviate_url()
            assert url == "http://weaviate:8080"  # Uses Docker service name

    def test_configuration_type_conversion(self):
        """Test proper type conversion for configuration values."""
        test_config = {
            'MAX_CHUNK_SIZE': '1500',  # String that should become int
            'CHUNK_OVERLAP': '150',     # String that should become int
            'ENABLE_TABLE_EXTRACTION': 'true',  # String that should become bool
            'LOG_FAILED_CHUNKS': 'false'  # String that should become bool
        }

        with patch.dict(os.environ, test_config):
            from src.config import get_typed_config

            config = get_typed_config()

            # Check type conversions
            assert isinstance(config['max_chunk_size'], int)
            assert config['max_chunk_size'] == 1500

            assert isinstance(config['chunk_overlap'], int)
            assert config['chunk_overlap'] == 150

            assert isinstance(config['enable_table_extraction'], bool)
            assert config['enable_table_extraction'] is True

            assert isinstance(config['log_failed_chunks'], bool)
            assert config['log_failed_chunks'] is False
