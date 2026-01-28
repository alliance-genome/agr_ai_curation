"""Configuration management for the AI Curation Prototype."""

import os
import logging
import socket
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Repository root (project root containing this backend/ directory)
REPO_ROOT = Path(__file__).resolve().parents[2]

# Load .env file from secure home directory location ONLY.
# This prevents accidental commits of secrets to the repository.
#
# REQUIRED location: ~/.agr_ai_curation/.env
#
# Setup:
#   mkdir -p ~/.agr_ai_curation
#   cp .env.example ~/.agr_ai_curation/.env
#   chmod 600 ~/.agr_ai_curation/.env

_env_loaded_from: Optional[str] = None

def _load_env_file() -> Optional[str]:
    """Load .env from secure home directory location only."""
    home = Path.home()
    env_path = home / '.agr_ai_curation' / '.env'

    if env_path.exists():
        load_dotenv(env_path)
        return str(env_path)

    return None

_env_loaded_from = _load_env_file()

logger = logging.getLogger(__name__)

# Warn if .env not found in required location
if not _env_loaded_from:
    print("[config] WARNING: No .env file found at ~/.agr_ai_curation/.env")
    print("[config] Copy .env.example to ~/.agr_ai_curation/.env and fill in values")


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing required values."""
    pass


def get_weaviate_url() -> str:
    """Get Weaviate connection URL from environment variables."""
    host = os.getenv('WEAVIATE_HOST', 'weaviate')
    port = os.getenv('WEAVIATE_PORT', '8080')
    scheme = os.getenv('WEAVIATE_SCHEME', 'http')
    return f"{scheme}://{host}:{port}"


def get_openai_api_key() -> Optional[str]:
    """Get OpenAI API key from environment."""
    return os.getenv('OPENAI_API_KEY', None)


def get_log_level() -> str:
    """Get log level from environment, default to INFO."""
    level = os.getenv('LOG_LEVEL', 'INFO').upper()
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

    if level not in valid_levels:
        logger.warning(f"Invalid log level '{level}', defaulting to INFO")
        return 'INFO'

    return level


def get_extraction_strategy() -> str:
    """Get PDF extraction strategy from environment."""
    return os.getenv('PDF_EXTRACTION_STRATEGY', 'fast')


def get_database_url() -> str:
    """Get primary PostgreSQL database connection URL."""
    db_user = os.getenv('POSTGRES_USER', 'postgres')
    db_pass = os.getenv('POSTGRES_PASSWORD', 'postgres')
    db_host = os.getenv('POSTGRES_HOST', 'postgres')
    db_port = os.getenv('POSTGRES_PORT', '5432')
    db_name = os.getenv('POSTGRES_DB', 'ai_curation')

    return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


def get_app_database_url() -> str:
    """Get the application database URL.

    This is the primary PostgreSQL database for storing all application data:
    users, pdf_documents, feedback_reports, curation_flows, audit_log, etc.

    Reads from DATABASE_URL environment variable, falling back to constructed
    URL from individual POSTGRES_* variables.
    """
    return os.getenv('DATABASE_URL', get_database_url())



def get_pdf_storage_path() -> Path:
    """Return filesystem path used for storing original PDF files."""
    raw_path = os.getenv('PDF_STORAGE_PATH', 'pdf_storage')
    path = Path(raw_path)

    if path.is_absolute():
        return path

    # Resolve relative to repository root so defaults map to /pdf_storage
    return (REPO_ROOT / path).resolve()


def get_docling_json_storage_path() -> Path:
    """Return filesystem path used for storing raw Docling JSON outputs."""
    raw_path = os.getenv('DOCLING_JSON_STORAGE_PATH', 'pdf_storage/docling_json')
    path = Path(raw_path)

    if path.is_absolute():
        return path

    # Resolve relative to repository root
    return (REPO_ROOT / path).resolve()


def get_processed_json_storage_path() -> Path:
    """Return filesystem path used for storing processed JSON before embedding."""
    raw_path = os.getenv('PROCESSED_JSON_STORAGE_PATH', 'pdf_storage/processed_json')
    path = Path(raw_path)

    if path.is_absolute():
        return path

    # Resolve relative to repository root
    return (REPO_ROOT / path).resolve()


def get_file_output_storage_path() -> Path:
    """Return filesystem path for storing generated file outputs (CSV, TSV, JSON).

    This is used by file output agents to store downloadable exports.
    The path supports both absolute paths (from environment) and relative paths
    (resolved relative to repository root).

    Returns:
        Path to file_outputs directory
    """
    raw_path = os.getenv('FILE_OUTPUT_STORAGE_PATH', 'file_outputs')
    path = Path(raw_path)

    if path.is_absolute():
        return path

    # Resolve relative to repository root
    return (REPO_ROOT / path).resolve()


def validate_extraction_strategy(strategy: str) -> None:
    """Validate that extraction strategy is valid."""
    valid_strategies = ['fast', 'auto', 'hi_res']
    if strategy not in valid_strategies:
        raise ValueError(f"Invalid extraction strategy: {strategy}. Must be one of {valid_strategies}")


def is_table_extraction_enabled() -> bool:
    """Check if table extraction is enabled."""
    value = os.getenv('ENABLE_TABLE_EXTRACTION', 'false')
    return value.lower() == 'true'


def get_chunk_config() -> Dict[str, int]:
    """Get chunk configuration from environment."""
    try:
        max_size = int(os.getenv('MAX_CHUNK_SIZE', '1000'))
    except ValueError:
        logger.warning("Invalid MAX_CHUNK_SIZE, using default 1000")
        max_size = 1000

    try:
        overlap = int(os.getenv('CHUNK_OVERLAP', '100'))
    except ValueError:
        logger.warning("Invalid CHUNK_OVERLAP, using default 100")
        overlap = 100

    return {
        'max_size': max_size,
        'overlap': overlap
    }


def should_log_failed_chunks() -> bool:
    """Check if failed chunk insertions should be logged."""
    value = os.getenv('LOG_FAILED_CHUNKS', 'true')
    # Default to True for safety - we want to know about failures
    if value.lower() in ['false', 'no', '0']:
        return False
    return True


def load_env_file(path: Optional[Path] = None) -> Dict[str, str]:
    """Load environment variables from a .env file."""
    if path is None:
        path = Path.cwd() / '.env'

    env_vars = {}
    if path.exists():
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip().strip('"\'')

    return env_vars


def validate_configuration(require_openai: bool = False) -> bool:
    """
    Validate that all required configuration is present.

    Args:
        require_openai: Whether OpenAI API key is required

    Returns:
        True if configuration is valid

    Raises:
        ConfigurationError: If required configuration is missing
    """
    errors = []

    # Check Weaviate configuration
    weaviate_url = get_weaviate_url()
    if not weaviate_url:
        errors.append("Weaviate URL configuration is missing")

    # Check OpenAI API key if required
    if require_openai:
        api_key = get_openai_api_key()
        if not api_key:
            errors.append("OPENAI_API_KEY is required but not set")

    # Validate extraction strategy
    try:
        strategy = get_extraction_strategy()
        validate_extraction_strategy(strategy)
    except ValueError as e:
        errors.append(str(e))

    # Validate chunk configuration
    chunk_config = get_chunk_config()
    if chunk_config['max_size'] <= 0:
        errors.append("MAX_CHUNK_SIZE must be positive")
    if chunk_config['overlap'] < 0:
        errors.append("CHUNK_OVERLAP cannot be negative")
    if chunk_config['overlap'] >= chunk_config['max_size']:
        errors.append("CHUNK_OVERLAP must be less than MAX_CHUNK_SIZE")

    if errors:
        raise ConfigurationError(f"Configuration validation failed:\n" + "\n".join(errors))

    return True


def get_typed_config() -> Dict[str, Any]:
    """Get all configuration with proper type conversion."""
    return {
        'weaviate_url': get_weaviate_url(),
        'openai_api_key': get_openai_api_key(),
        'log_level': get_log_level(),
        'extraction_strategy': get_extraction_strategy(),
        'enable_table_extraction': is_table_extraction_enabled(),
        'max_chunk_size': get_chunk_config()['max_size'],
        'chunk_overlap': get_chunk_config()['overlap'],
        'log_failed_chunks': should_log_failed_chunks(),
        'database_url': get_app_database_url(),
        'pdf_storage_path': str(get_pdf_storage_path())
    }


# Okta functions REMOVED - RIP AND REPLACE with Cognito
# No fallback support needed


# AWS Cognito Configuration (Cognito Migration)
def get_cognito_region() -> Optional[str]:
    """Get AWS Cognito region from environment (e.g., 'us-east-1')."""
    return os.getenv('COGNITO_REGION', 'us-east-1')


def get_cognito_user_pool_id() -> Optional[str]:
    """Get Cognito User Pool ID from environment (e.g., 'us-east-1_XXXXXXXXX')."""
    return os.getenv('COGNITO_USER_POOL_ID', None)


def get_cognito_client_id() -> Optional[str]:
    """Get Cognito App Client ID from environment."""
    return os.getenv('COGNITO_CLIENT_ID', None)


def get_cognito_client_secret() -> Optional[str]:
    """Get Cognito App Client Secret from environment."""
    return os.getenv('COGNITO_CLIENT_SECRET', None)


def get_cognito_redirect_uri() -> Optional[str]:
    """Get Cognito redirect URI from environment."""
    return os.getenv('COGNITO_REDIRECT_URI', 'http://localhost:3002/auth/callback')


def get_cognito_domain() -> str:
    """Get Cognito custom domain for Hosted UI from environment.

    This is the domain where Cognito's Hosted UI is hosted (e.g., https://auth.alliancegenome.org).
    Used for constructing OAuth2 authorization and token endpoints.

    Returns:
        Cognito custom domain URL (default: https://auth.alliancegenome.org)
    """
    return os.getenv('COGNITO_DOMAIN', 'https://auth.alliancegenome.org')


def is_cognito_configured() -> bool:
    """Check if AWS Cognito authentication is configured.

    Returns True if COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are set.
    """
    pool_id = get_cognito_user_pool_id()
    client_id = get_cognito_client_id()
    return bool(pool_id and client_id)


def is_running_on_ec2() -> bool:
    """Detect if application is running on an AWS EC2 instance.

    Uses multiple detection methods:
    1. Check EC2 instance metadata service (IMDS)
    2. Check for EC2-specific hostname patterns
    3. Check for RUNNING_ON_EC2 environment variable (explicit override)

    Returns:
        True if definitely running on EC2, False otherwise
    """
    # Check explicit environment variable first (allows forcing EC2 mode for safety)
    if os.getenv('RUNNING_ON_EC2', '').lower() == 'true':
        return True

    # Check hostname patterns common on EC2
    try:
        hostname = socket.gethostname()
        # EC2 hostnames typically look like ip-172-31-29-141 or i-xxxxx
        if hostname.startswith('ip-') or hostname.startswith('i-'):
            return True
    except Exception:
        pass

    # Try EC2 instance metadata service (IMDSv2 with token)
    try:
        # Get IMDSv2 token first
        token_req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
            method='PUT'
        )
        with urllib.request.urlopen(token_req, timeout=1) as response:
            token = response.read().decode('utf-8')

        # Use token to get instance ID
        metadata_req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/instance-id',
            headers={'X-aws-ec2-metadata-token': token}
        )
        with urllib.request.urlopen(metadata_req, timeout=1) as response:
            instance_id = response.read().decode('utf-8')
            if instance_id.startswith('i-'):
                return True
    except Exception:
        pass

    return False


def _get_ec2_instance_metadata() -> tuple[str | None, str | None]:
    """Get EC2 instance ID and region from metadata service.

    Returns:
        Tuple of (instance_id, region) or (None, None) if not on EC2
    """
    try:
        # Get IMDSv2 token
        token_req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
            method='PUT'
        )
        with urllib.request.urlopen(token_req, timeout=1) as response:
            token = response.read().decode('utf-8')

        # Get instance ID
        id_req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/instance-id',
            headers={'X-aws-ec2-metadata-token': token}
        )
        with urllib.request.urlopen(id_req, timeout=1) as response:
            instance_id = response.read().decode('utf-8')

        # Get region from availability zone
        az_req = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/placement/availability-zone',
            headers={'X-aws-ec2-metadata-token': token}
        )
        with urllib.request.urlopen(az_req, timeout=1) as response:
            az = response.read().decode('utf-8')
            # Region is AZ minus the last character (e.g., us-east-1a -> us-east-1)
            region = az[:-1] if az else None

        return instance_id, region
    except Exception:
        return None, None


def _check_ec2_tag(tag_key: str, expected_value: str) -> bool:
    """Check if the current EC2 instance has a specific tag value.

    This uses the EC2 API to check instance tags, which is more secure than
    environment variables since tags can only be set via AWS console/CLI.

    Args:
        tag_key: The tag key to check (e.g., "AllowDevMode")
        expected_value: The expected tag value (e.g., "true")

    Returns:
        True if the instance has the tag with the expected value, False otherwise
    """
    instance_id, region = _get_ec2_instance_metadata()
    if not instance_id or not region:
        return False

    try:
        import boto3
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_tags(
            Filters=[
                {'Name': 'resource-id', 'Values': [instance_id]},
                {'Name': 'key', 'Values': [tag_key]},
            ]
        )
        for tag in response.get('Tags', []):
            if tag.get('Key') == tag_key and tag.get('Value', '').lower() == expected_value.lower():
                return True
    except Exception as e:
        logger.debug("Failed to check EC2 tag %s: %s", tag_key, e)
    return False


# Cache EC2 detection and dev mode permission results
_ec2_detection_cache = None
_dev_mode_allowed_cache = None


def _get_ec2_status() -> bool:
    """Get cached EC2 detection status."""
    global _ec2_detection_cache
    if _ec2_detection_cache is None:
        _ec2_detection_cache = is_running_on_ec2()
    return _ec2_detection_cache


def _is_dev_mode_allowed_on_ec2() -> bool:
    """Check if dev mode is allowed on this EC2 instance via instance tag.

    Looks for the EC2 tag: AllowDevMode=true

    This is more secure than environment variables because:
    - Tags are set on the instance itself, not in config files
    - Can't be accidentally copied between environments
    - Requires AWS console/CLI access to modify
    """
    global _dev_mode_allowed_cache
    if _dev_mode_allowed_cache is None:
        _dev_mode_allowed_cache = _check_ec2_tag('AllowDevMode', 'true')
        if _dev_mode_allowed_cache:
            logger.warning(
                "EC2 tag AllowDevMode=true detected - dev mode is ALLOWED on this instance. "
                "Ensure this is NOT a production server!"
            )
    return _dev_mode_allowed_cache


def is_dev_mode() -> bool:
    """Check if application is running in development mode.

    When DEV_MODE=true, authentication is bypassed and a mock user is used.
    This simplifies local development without requiring Cognito configuration.

    SECURITY: Dev mode is BLOCKED on EC2 instances by default to prevent
    unauthorized access to LLM endpoints. Anyone could hit the public URL
    and run requests through the LLM if dev mode were allowed on production.

    To enable dev mode on a dev/staging EC2 instance, add this EC2 tag:
        AllowDevMode = true

    This is safer than environment variables because tags are tied to the
    instance and can't be accidentally copied via config files.

    Returns:
        True if DEV_MODE=true AND (not on EC2 OR instance has AllowDevMode=true tag)
        False otherwise
    """
    # SECURITY CHECK: Block dev mode on EC2 instances unless tag allows it
    if _get_ec2_status():
        if not _is_dev_mode_allowed_on_ec2():
            # Only log once on first check
            if _dev_mode_allowed_cache is False:
                pass  # Already logged in _is_dev_mode_allowed_on_ec2
            elif _dev_mode_allowed_cache is None:
                logger.warning(
                    "EC2 environment detected - DEV_MODE is BLOCKED for security. "
                    "Add EC2 tag 'AllowDevMode=true' to enable dev mode on this instance."
                )
            return False

    value = os.getenv('DEV_MODE', 'false')
    return value.lower() == 'true'


def get_secure_cookies() -> bool:
    """Check if secure cookies should be used for authentication.

    When SECURE_COOKIES=true, cookies will only be sent over HTTPS connections.
    Set to false for local development (http://localhost), true for production (HTTPS).

    Security Note: In production with HTTPS, this MUST be set to true to prevent
    token interception over unencrypted connections.

    Returns:
        True if SECURE_COOKIES environment variable is set to 'true' (case-insensitive)
    """
    value = os.getenv('SECURE_COOKIES', 'false')
    return value.lower() == 'true'


def get_env_source() -> Optional[str]:
    """Get the path from which .env was loaded.

    Returns:
        Path string if .env was loaded, None if no .env file was found.

    Useful for debugging configuration issues.
    """
    return _env_loaded_from


def print_configuration(mask_secrets: bool = True):
    """Print current configuration for debugging."""
    config = get_typed_config()

    print("Current Configuration:")
    print("-" * 40)
    env_source = get_env_source()
    if env_source:
        print(f"env_source: {env_source}")
    else:
        print("env_source: (no .env file loaded)")
    for key, value in config.items():
        if mask_secrets and 'key' in key.lower() and value:
            # Mask API keys
            masked_value = value[:8] + "..." if len(value) > 8 else "***"
            print(f"{key}: {masked_value}")
        else:
            print(f"{key}: {value}")
    print("-" * 40)
