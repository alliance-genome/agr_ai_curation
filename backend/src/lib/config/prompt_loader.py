"""
Prompt Loader for Config-Driven Architecture.

This module loads prompts from YAML files into the database at startup.
YAML files are the source of truth; the database is a runtime cache.

Flow:
1. Scan config/agents/*/prompt.yaml for base prompts
2. Scan config/agents/*/group_rules/*.yaml for group-specific rules
3. Upsert into prompt_templates table (YAML overwrites DB)
4. cache.py then loads from database as usual

Usage:
    from src.lib.config.prompt_loader import load_prompts

    # At startup, before cache initialization
    with get_db() as db:
        counts = load_prompts(db=db)
        print(f"Loaded {counts['base_prompts']} base prompts, {counts['group_rules']} group rules")

Multi-worker safety:
    Uses PostgreSQL advisory lock to ensure only one worker loads prompts
    at a time, preventing duplicate versions or race conditions.
"""

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)

# Advisory lock ID for prompt loading (arbitrary unique number)
PROMPT_LOADER_LOCK_ID = 948572631


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for pyproject.toml or docker-compose.yml.

    Returns:
        Path to project root directory, or None if not found
    """
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() or (parent / "docker-compose.yml").exists():
            return parent
    return None


def _get_default_agents_path() -> Path:
    """Get the default agents path, trying multiple strategies.

    Order of precedence:
    1. AGENTS_CONFIG_PATH environment variable
    2. Project root detection (pyproject.toml or docker-compose.yml)
    3. Relative path from this module (fallback for Docker)

    Returns:
        Path to agents directory
    """
    # Strategy 1: Environment variable
    env_path = os.environ.get("AGENTS_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Strategy 2: Project root detection
    project_root = _find_project_root()
    if project_root:
        return project_root / "config" / "agents"

    # Strategy 3: Relative path fallback (for Docker where backend is at /app/backend)
    return Path(__file__).parent.parent.parent.parent.parent / "config" / "agents"


# Thread safety lock for initialization (process-local)
_init_lock = threading.Lock()
_initialized: bool = False


def _content_hash(content: str) -> str:
    """Generate a hash of prompt content for comparison.

    Args:
        content: The prompt content string

    Returns:
        SHA256 hash of the content (first 16 chars)
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _upsert_prompt(
    db: Session,
    agent_name: str,
    prompt_type: str,
    content: str,
    group_id: Optional[str] = None,
    source_file: Optional[str] = None,
    description: Optional[str] = None,
) -> Tuple[bool, int]:
    """
    Upsert a prompt into the database.

    Logic:
    1. Check if active prompt exists with same agent_name/prompt_type/group_id
    2. If exists and content matches (hash), do nothing
    3. If exists and content differs, deactivate old, create new version
    4. If not exists, create version 1

    Args:
        db: Database session
        agent_name: Short agent name (e.g., "gene", "pdf")
        prompt_type: "system" for base prompts, "group_rules" for group rules
        content: The prompt content
        group_id: None for base prompts, group ID for group rules
        source_file: Path to source YAML file (for provenance)
        description: Optional description

    Returns:
        Tuple of (created: bool, version: int)
        - created=True if new version was created
        - version is the current active version number
    """
    # Find current active prompt
    query = db.query(PromptTemplate).filter(
        PromptTemplate.agent_name == agent_name,
        PromptTemplate.prompt_type == prompt_type,
        PromptTemplate.is_active == True,
    )

    if group_id is not None:
        query = query.filter(PromptTemplate.group_id == group_id)
    else:
        query = query.filter(PromptTemplate.group_id.is_(None))

    existing = query.first()

    # Compare content by hash
    new_hash = _content_hash(content)

    if existing:
        existing_hash = _content_hash(existing.content)

        if new_hash == existing_hash:
            # Content unchanged, do nothing
            logger.debug(
                f"Prompt unchanged: {agent_name}:{prompt_type}"
                f"{f'/{group_id}' if group_id else ''} v{existing.version}"
            )
            return (False, existing.version)

        # Content changed - deactivate old, create new version
        existing.is_active = False
        new_version = existing.version + 1

        logger.info(
            f"Prompt updated: {agent_name}:{prompt_type}"
            f"{f'/{group_id}' if group_id else ''} v{existing.version} -> v{new_version}"
        )
    else:
        # No existing prompt - create version 1
        new_version = 1
        logger.info(
            f"Prompt created: {agent_name}:{prompt_type}"
            f"{f'/{group_id}' if group_id else ''} v{new_version}"
        )

    # Create new prompt
    new_prompt = PromptTemplate(
        agent_name=agent_name,
        prompt_type=prompt_type,
        group_id=group_id,
        content=content,
        version=new_version,
        is_active=True,
        source_file=source_file,
        description=description,
        change_notes="Loaded from YAML at startup",
    )

    db.add(new_prompt)
    return (True, new_version)


def _load_base_prompt(
    agent_folder: Path, db: Session, project_root: Optional[Path] = None
) -> Optional[str]:
    """Load a single prompt.yaml file into the database.

    Args:
        agent_folder: Path to agent folder (e.g., config/agents/gene/)
        db: Database session
        project_root: Pre-computed project root for source_file paths

    Returns:
        Agent name if loaded successfully, None otherwise

    Note:
        The agent_name is derived from the folder name, which is the canonical
        identifier matching AGENT_REGISTRY keys (e.g., "gene", "go_annotations",
        "csv_formatter"). This avoids brittle string heuristics.
    """
    prompt_yaml = agent_folder / "prompt.yaml"

    if not prompt_yaml.exists():
        logger.debug('No prompt.yaml in %s', agent_folder.name)
        return None

    try:
        with open(prompt_yaml, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            logger.warning('Empty prompt.yaml in %s', agent_folder.name)
            return None

        # Extract fields
        content = data.get("content")

        if not content:
            logger.warning('Missing content in %s', prompt_yaml)
            return None

        # Use folder name as agent_name - this IS the canonical identifier
        # that matches AGENT_REGISTRY keys (e.g., "gene", "go_annotations")
        agent_name = agent_folder.name

        # Validate agent_id if present (informational warning for debugging)
        yaml_agent_id = data.get("agent_id")
        if yaml_agent_id and yaml_agent_id != agent_name:
            logger.warning(
                f"agent_id mismatch in {agent_folder.name}/prompt.yaml: "
                f"agent_id='{yaml_agent_id}' but folder name is '{agent_name}'. "
                f"Using folder name as canonical agent_name."
            )

        # Calculate relative path for source_file
        try:
            if project_root:
                source_file = str(prompt_yaml.relative_to(project_root))
            else:
                source_file = str(prompt_yaml)
        except ValueError:
            source_file = str(prompt_yaml)

        # Upsert into database
        _upsert_prompt(
            db=db,
            agent_name=agent_name,
            prompt_type="system",
            content=content,
            group_id=None,
            source_file=source_file,
        )

        return agent_name

    except yaml.YAMLError as e:
        logger.error('Failed to parse %s: %s', prompt_yaml, e)
        raise
    except Exception as e:
        logger.error('Failed to load prompt from %s: %s', agent_folder.name, e)
        raise


def _load_group_rules(
    agent_folder: Path,
    agent_name: str,
    db: Session,
    project_root: Optional[Path] = None,
) -> int:
    """Load all group_rules/*.yaml for an agent.

    Args:
        agent_folder: Path to agent folder
        agent_name: Agent name (e.g., "gene", "go_annotations")
        db: Database session
        project_root: Pre-computed project root for source_file paths

    Returns:
        Number of group rules loaded
    """
    group_rules_dir = agent_folder / "group_rules"

    if not group_rules_dir.exists():
        return 0

    count = 0

    for rule_file in sorted(group_rules_dir.glob("*.yaml")):
        # Skip example files
        if rule_file.name.startswith("_") or rule_file.name == "example.yaml":
            continue

        try:
            with open(rule_file, "r") as f:
                data = yaml.safe_load(f)

            if not data:
                logger.warning('Empty group rules file: %s', rule_file)
                continue

            # Extract fields
            group_id = data.get("group_id")
            content = data.get("content")

            if not group_id:
                # Try to infer from filename (e.g., fb.yaml -> FB)
                group_id = rule_file.stem.upper()
                logger.debug("Inferred group_id '%s' from filename %s", group_id, rule_file.name)

            if not content:
                logger.warning('Missing content in %s', rule_file)
                continue

            # Calculate relative path for source_file
            try:
                if project_root:
                    source_file = str(rule_file.relative_to(project_root))
                else:
                    source_file = str(rule_file)
            except ValueError:
                source_file = str(rule_file)

            # Upsert into database
            _upsert_prompt(
                db=db,
                agent_name=agent_name,
                prompt_type="group_rules",
                content=content,
                group_id=group_id,
                source_file=source_file,
            )

            count += 1

        except yaml.YAMLError as e:
            logger.error('Failed to parse %s: %s', rule_file, e)
            raise
        except Exception as e:
            logger.error('Failed to load group rule from %s: %s', rule_file, e)
            raise

    return count


def _acquire_advisory_lock(db: Session) -> tuple[bool, bool]:
    """Acquire PostgreSQL advisory lock for multi-worker safety.

    Strategy for multi-worker consistency:
    1. Try non-blocking lock first (pg_try_advisory_lock)
    2. If lock acquired, we're the loader - return (True, True)
    3. If lock NOT acquired, another worker is loading - wait for them
    4. Block on pg_advisory_lock until other worker releases
    5. After acquiring, return (True, False) to indicate "waited for loader"

    This ensures ALL workers wait for loading to complete before
    initializing cache, preventing stale/empty cache issues.

    Args:
        db: Database session

    Returns:
        Tuple of (lock_acquired: bool, is_loader: bool)
        - lock_acquired: True if we hold the lock
        - is_loader: True if we should load prompts, False if we waited for another loader
    """
    try:
        # First, try non-blocking lock
        result = db.execute(
            text(f"SELECT pg_try_advisory_lock({PROMPT_LOADER_LOCK_ID})")
        )
        got_lock = result.scalar()

        if got_lock:
            # We're the loader
            return (True, True)

        # Another worker is loading - wait for them to finish
        logger.info("Waiting for another worker to finish loading prompts...")
        db.execute(text(f"SELECT pg_advisory_lock({PROMPT_LOADER_LOCK_ID})"))
        logger.info("Other worker finished, acquired lock")

        # We got the lock after waiting - we're NOT the loader
        # (prompts were already loaded by the other worker)
        return (True, False)

    except Exception as e:
        # If advisory lock fails (e.g., SQLite in tests), proceed as loader
        logger.debug('Advisory lock not available (non-PostgreSQL?): %s', e)
        return (True, True)


def _release_advisory_lock(db: Session) -> None:
    """Release PostgreSQL advisory lock.

    Args:
        db: Database session
    """
    try:
        db.execute(text(f"SELECT pg_advisory_unlock({PROMPT_LOADER_LOCK_ID})"))
    except Exception:
        # Ignore errors on unlock (lock may not exist in non-PostgreSQL)
        pass


def load_prompts(
    agents_path: Optional[Path] = None,
    db: Session = None,
    force_reload: bool = False,
) -> Dict[str, int]:
    """
    Load all prompts from YAML files into the database.

    This function scans the agents directory and loads:
    - config/agents/*/prompt.yaml -> base prompts (prompt_type="system", group_id=NULL)
    - config/agents/*/group_rules/*.yaml -> group rules (prompt_type="group_rules")

    The database is treated as a cache - YAML files are the source of truth.
    Content is compared by hash; unchanged prompts don't create new versions.

    Multi-worker safety:
        Uses PostgreSQL advisory lock to coordinate loading across workers.
        If another worker is loading, this worker WAITS for completion
        (blocking lock) to ensure cache is initialized from loaded data.

    Thread safety:
        Uses process-local lock for thread safety within a single process.

    Args:
        agents_path: Path to agents directory (default: config/agents/)
        db: Database session (required)
        force_reload: Force reload even if already initialized

    Returns:
        Dictionary with counts: {"base_prompts": N, "group_rules": M}
        If skipped (already initialized or waited for another loader):
        {"base_prompts": 0, "group_rules": 0, "skipped": True}

    Raises:
        ValueError: If db is not provided
        FileNotFoundError: If agents_path doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    global _initialized

    if db is None:
        raise ValueError("Database session is required for prompt loading")

    # Thread-safe initialization (process-local)
    with _init_lock:
        if _initialized and not force_reload:
            logger.debug("Prompt loader already initialized, skipping")
            return {"base_prompts": 0, "group_rules": 0, "skipped": True}

        # Resolve agents_path lazily (allows env var changes after import)
        if agents_path is None:
            agents_path = _get_default_agents_path()

        if not agents_path.exists():
            raise FileNotFoundError(f"Agents directory not found: {agents_path}")

        # Multi-worker safety: acquire advisory lock
        # This may block if another worker is loading (ensures cache consistency)
        _, is_loader = _acquire_advisory_lock(db)

        if not is_loader:
            # We waited for another worker to finish loading
            # Release lock and skip - prompts are already loaded
            _release_advisory_lock(db)
            _initialized = True  # Mark as initialized since other worker loaded
            logger.info("Prompts loaded by another worker, skipping")
            return {"base_prompts": 0, "group_rules": 0, "skipped": True}

        try:
            # Always sync YAML to database - content-hash comparison ensures:
            # - Unchanged prompts: no new version created (hash match → no-op)
            # - Changed prompts: old version deactivated, new version created
            # - New prompts: version 1 created
            # This maintains YAML as the source of truth on every startup.
            logger.info('Loading prompts from YAML: %s', agents_path)

            # Compute project root once for all source_file paths
            project_root = _find_project_root()

            base_prompt_count = 0
            group_rules_count = 0

            # Scan for agent folders
            for folder in sorted(agents_path.iterdir()):
                # Skip non-directories and underscore-prefixed folders
                if not folder.is_dir() or folder.name.startswith("_"):
                    continue

                # Load base prompt (using folder name as agent_name)
                agent_name = _load_base_prompt(folder, db, project_root)

                if agent_name:
                    base_prompt_count += 1

                    # Load group rules for this agent
                    rules_count = _load_group_rules(
                        folder, agent_name, db, project_root
                    )
                    group_rules_count += rules_count

            # Commit all changes
            db.commit()

            _initialized = True

            logger.info(
                f"Prompt loader complete: {base_prompt_count} base prompts, "
                f"{group_rules_count} group rules"
            )

            return {"base_prompts": base_prompt_count, "group_rules": group_rules_count}

        finally:
            # Always release advisory lock
            _release_advisory_lock(db)


def is_initialized() -> bool:
    """Check if prompts have been loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset the initialization flag (for testing)."""
    global _initialized
    _initialized = False
