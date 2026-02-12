"""
AGR Curation Database Client Helper

Provides production-ready access to the AGR Curation database using the
agr-curation-api-client package with direct database access mode.

This module handles:
- Environment setup for fastapi_okta dependency (from agr-curation-api-client, not ours)
- AWS Secrets Manager credential retrieval
- Database configuration with tunnel support
- Singleton DatabaseMethods instance for efficient connection pooling

Usage in agents:
    from src.lib.database.agr_client import get_agr_db_client

    # Get database client
    db = get_agr_db_client()

    # Query genes
    genes = db.get_genes_by_taxon('NCBITaxon:6239', limit=10)

    # Query disease annotations
    annotations = db.get_disease_annotations('NCBITaxon:6239')
"""

import os
import tempfile
import json
import logging
from typing import Optional, Dict, Any
from functools import lru_cache

# CRITICAL: Set TMP_PATH BEFORE importing agr_curation_api
# The agr-curation-api-client package has a fastapi_okta dependency (external, not ours)
# that requires this environment variable at import time
if 'TMP_PATH' not in os.environ:
    os.environ['TMP_PATH'] = tempfile.mkdtemp()

# Now safe to import from agr_curation_api
from agr_curation_api.db_methods import DatabaseConfig, DatabaseMethods

logger = logging.getLogger(__name__)

# Singleton instance
_db_client: Optional[DatabaseMethods] = None


def get_aws_credentials(secret_id: str = 'ai-curation/db/curation-readonly',
                       aws_profile: Optional[str] = None,
                       region: str = 'us-east-1') -> Dict[str, Any]:
    """
    Fetch database credentials from AWS Secrets Manager.

    Args:
        secret_id: AWS Secrets Manager secret ID
        aws_profile: AWS profile name (optional, uses default if not provided)
        region: AWS region

    Returns:
        Dictionary with keys: username, password, dbname, host (optional), port (optional)

    Raises:
        Exception: If credential retrieval fails
    """
    try:
        import boto3

        # Create session with profile if specified
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
        else:
            session = boto3.Session()

        client = session.client('secretsmanager', region_name=region)
        response = client.get_secret_value(SecretId=secret_id)
        secret = json.loads(response['SecretString'])

        logger.info('Retrieved credentials from AWS Secrets Manager: %s', secret_id)
        logger.debug('Database user: %s', secret.get('username'))

        return secret

    except Exception as e:
        logger.error('Failed to retrieve credentials from AWS Secrets Manager: %s', e)
        raise


def create_database_config(
    secret: Optional[Dict[str, Any]] = None,
    host: str = 'localhost',
    port: str = '5433',
    aws_profile: Optional[str] = None
) -> DatabaseConfig:
    """
    Create DatabaseConfig for AGR Curation database.

    Args:
        secret: Pre-fetched credentials dict (if None, will try CURATION_DB_URL env var, then AWS)
        host: Database host (default: localhost for tunnel)
        port: Database port (default: 5433 for curation tunnel)
        aws_profile: AWS profile for credential retrieval

    Returns:
        Configured DatabaseConfig instance

    Notes:
        - Prefers CURATION_DB_URL environment variable (for dev mode with tunnels)
        - Falls back to AWS Secrets Manager if CURATION_DB_URL not set
        - Default host/port assume SSH tunnel is active (localhost:5433)
    """
    # Try environment variable first (dev mode with .env file)
    curation_db_url = os.getenv('CURATION_DB_URL')
    if curation_db_url and secret is None:
        try:
            # Parse postgresql://user:pass@host:port/dbname
            from urllib.parse import urlparse
            parsed = urlparse(curation_db_url)

            config = DatabaseConfig()
            config.username = parsed.username
            config.password = parsed.password
            config.database = parsed.path.lstrip('/')
            config.host = parsed.hostname
            config.port = str(parsed.port) if parsed.port else '5432'

            logger.info('Using CURATION_DB_URL environment variable: %s:%s/%s', config.host, config.port, config.database)
            return config
        except Exception as e:
            logger.warning('Failed to parse CURATION_DB_URL, will try AWS Secrets Manager: %s', e)

    # Fetch credentials from AWS if not provided and CURATION_DB_URL not available
    if secret is None:
        secret = get_aws_credentials(aws_profile=aws_profile)

    # Create config from AWS credentials
    config = DatabaseConfig()
    config.username = secret['username']
    config.password = secret['password']
    config.database = secret.get('dbname', 'curation')
    config.host = host
    config.port = port

    logger.info('Using AWS credentials: %s:%s/%s', config.host, config.port, config.database)

    return config


@lru_cache(maxsize=1)
def get_agr_db_client(
    host: str = 'localhost',
    port: str = '5433',
    aws_profile: Optional[str] = None,
    force_new: bool = False
) -> DatabaseMethods:
    """
    Get singleton DatabaseMethods instance for AGR Curation database.

    This function uses caching to return the same instance across calls,
    which enables efficient connection pooling.

    Args:
        host: Database host (default: localhost for tunnel)
        port: Database port (default: 5433 for curation tunnel)
        aws_profile: AWS profile for credential retrieval
        force_new: Force creation of new instance (clears cache)

    Returns:
        DatabaseMethods instance ready for queries

    Examples:
        # Basic usage (assumes tunnel active on localhost:5433)
        db = get_agr_db_client()
        genes = db.get_genes_by_taxon('NCBITaxon:6239', limit=10)

        # Custom configuration
        db = get_agr_db_client(host='prod-db.example.com', port='5432')

        # Force new connection
        db = get_agr_db_client(force_new=True)

    Environment Variables:
        PERSISTENT_STORE_DB_HOST: Override default host
        PERSISTENT_STORE_DB_PORT: Override default port
        PERSISTENT_STORE_DB_NAME: Database name
        PERSISTENT_STORE_DB_USERNAME: Database username
        PERSISTENT_STORE_DB_PASSWORD: Database password
        CURATION_DB_URL: Full connection string (takes precedence)
    """
    global _db_client

    # Check for PERSISTENT_STORE_DB_* environment variables (for integration tests)
    env_host = os.getenv('PERSISTENT_STORE_DB_HOST')
    env_port = os.getenv('PERSISTENT_STORE_DB_PORT')
    env_name = os.getenv('PERSISTENT_STORE_DB_NAME')
    env_user = os.getenv('PERSISTENT_STORE_DB_USERNAME')
    env_password = os.getenv('PERSISTENT_STORE_DB_PASSWORD')

    # If all required env vars are set, build CURATION_DB_URL
    if env_host and env_port and env_name and env_user and env_password:
        # Create connection URL from individual env vars
        import urllib.parse
        encoded_password = urllib.parse.quote(env_password, safe='')
        curation_db_url = f"postgresql://{env_user}:{encoded_password}@{env_host}:{env_port}/{env_name}"
        os.environ['CURATION_DB_URL'] = curation_db_url
        logger.info('Using PERSISTENT_STORE_DB_* env vars: %s:%s/%s', env_host, env_port, env_name)

    # Clear cache if force_new requested
    if force_new:
        if _db_client is not None:
            try:
                _db_client.close()
                logger.info("Closed existing database connection")
            except Exception as e:
                logger.warning('Error closing database connection: %s', e)
        _db_client = None
        get_agr_db_client.cache_clear()

    # Return cached instance if available
    if _db_client is not None:
        return _db_client

    # Create new instance
    try:
        config = create_database_config(host=host, port=port, aws_profile=aws_profile)
        _db_client = DatabaseMethods(config)
        logger.info("Created new AGR database client instance")
        return _db_client

    except Exception as e:
        logger.error('Failed to create AGR database client: %s', e)
        raise


def close_agr_db_client():
    """
    Close the singleton database client connection.

    Call this during application shutdown to properly close database connections.
    """
    global _db_client

    if _db_client is not None:
        try:
            _db_client.close()
            logger.info("Closed AGR database client")
        except Exception as e:
            logger.warning('Error closing database client: %s', e)
        finally:
            _db_client = None
            get_agr_db_client.cache_clear()


# Convenience functions for common queries

def get_genes_for_species(taxon_id: str, limit: Optional[int] = None) -> list:
    """
    Convenience function to get genes for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:6239' for C. elegans)
        limit: Maximum number of genes to return

    Returns:
        List of Gene objects
    """
    db = get_agr_db_client()
    return db.get_genes_by_taxon(taxon_id, limit=limit)


def get_disease_annotations_for_species(taxon_id: str) -> list:
    """
    Convenience function to get disease annotations for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:6239' for C. elegans)

    Returns:
        List of dictionaries with disease annotation data
    """
    db = get_agr_db_client()
    return db.get_disease_annotations(taxon_id)


def get_expression_annotations_for_species(taxon_id: str) -> list:
    """
    Convenience function to get expression annotations for a species.

    Args:
        taxon_id: NCBI Taxon ID (e.g., 'NCBITaxon:6239' for C. elegans)

    Returns:
        List of dictionaries with expression annotation data
    """
    db = get_agr_db_client()
    return db.get_expression_annotations(taxon_id)


def get_available_species() -> list:
    """
    Convenience function to get list of available species.

    Returns:
        List of tuples: (species_abbreviation, taxon_id)
        Example: [('WB', 'NCBITaxon:6239'), ('FB', 'NCBITaxon:7227'), ...]
    """
    db = get_agr_db_client()
    return db.get_data_providers()
