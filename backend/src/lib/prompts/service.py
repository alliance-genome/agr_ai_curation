"""Prompt service for writes and execution logging.

The cache module handles reads. This service handles:
- Creating new prompt versions
- Activating specific versions
- Logging prompt usage (execution audit trail)

Usage:
    from src.lib.prompts.service import PromptService
    from src.models.sql.database import SessionLocal

    db = SessionLocal()
    try:
        service = PromptService(db)

        # Log prompt usage
        service.log_prompt_usage(
            prompt=prompt_template,
            trace_id="abc123",
            session_id="session_xyz",
        )

        # Create new version
        new_prompt = service.create_version(
            agent_name="pdf",
            content="New prompt text...",
            created_by="admin@example.com",
            change_notes="Improved extraction accuracy",
            activate=True,
        )

        db.commit()
    finally:
        db.close()
"""

from typing import Optional, List
from uuid import UUID
from sqlalchemy import func
from sqlalchemy.orm import Session
import logging

from .models import PromptTemplate, PromptExecutionLog
from .cache import refresh as refresh_cache

logger = logging.getLogger(__name__)


class PromptService:
    """Service for prompt writes and execution logging.

    For reads, use the cache module directly:
        from src.lib.prompts.cache import get_prompt
    """

    def __init__(self, db: Session):
        self.db = db

    def log_prompt_usage(
        self,
        prompt: PromptTemplate,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        flow_execution_id: Optional[UUID] = None,
    ) -> PromptExecutionLog:
        """Record that a prompt was used in an execution.

        Called once per prompt used. For agents with group rules,
        call twice: once for base prompt, once for group rule.

        Args:
            prompt: The PromptTemplate that was used
            trace_id: Langfuse trace ID (optional)
            session_id: Chat session ID (optional)
            flow_execution_id: Curation flow execution ID (optional, for future)

        Returns:
            The created PromptExecutionLog entry
        """
        log_entry = PromptExecutionLog(
            trace_id=trace_id,
            session_id=session_id,
            flow_execution_id=flow_execution_id,
            prompt_template_id=prompt.id,
            agent_name=prompt.agent_name,
            prompt_type=prompt.prompt_type,
            group_id=prompt.group_id,
            prompt_version=prompt.version,
        )
        self.db.add(log_entry)
        return log_entry

    def log_all_used_prompts(
        self,
        prompts: List[PromptTemplate],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        flow_execution_id: Optional[UUID] = None,
    ) -> List[PromptExecutionLog]:
        """Log multiple prompts that were used in an execution.

        Convenience method to log all prompts from get_used_prompts().

        Args:
            prompts: List of PromptTemplate objects that were used
            trace_id: Langfuse trace ID (optional)
            session_id: Chat session ID (optional)
            flow_execution_id: Curation flow execution ID (optional)

        Returns:
            List of created PromptExecutionLog entries
        """
        entries = []
        for prompt in prompts:
            entry = self.log_prompt_usage(
                prompt=prompt,
                trace_id=trace_id,
                session_id=session_id,
                flow_execution_id=flow_execution_id,
            )
            entries.append(entry)
        return entries

    def create_version(
        self,
        agent_name: str,
        content: str,
        prompt_type: str = "system",
        group_id: Optional[str] = None,
        created_by: Optional[str] = None,
        change_notes: Optional[str] = None,
        source_file: Optional[str] = None,
        description: Optional[str] = None,
        activate: bool = False,
    ) -> PromptTemplate:
        """Create a new version of a prompt.

        Note: Caller must commit the session after calling this method.

        Args:
            agent_name: Catalog ID (e.g., 'pdf', 'gene', 'supervisor')
            content: The prompt text
            prompt_type: e.g., 'system', 'group_rules' (default: 'system')
            group_id: NULL for base prompts, e.g., 'FB' for group rules
            created_by: Email or ID of creator (optional)
            change_notes: Why this version was created (optional)
            source_file: Original file path for provenance (optional)
            description: Description of the prompt (optional)
            activate: If True, make this the active version (default: False)

        Returns:
            The created PromptTemplate
        """
        # Get next version number (scoped to agent_name + prompt_type + group_id)
        query = self.db.query(func.max(PromptTemplate.version)).filter(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
        )
        if group_id is None:
            query = query.filter(PromptTemplate.group_id.is_(None))
        else:
            query = query.filter(PromptTemplate.group_id == group_id)

        max_version = query.scalar() or 0
        new_version = max_version + 1

        # If activating, deactivate current active version (same scope)
        if activate:
            self._deactivate_current(agent_name, prompt_type, group_id)

        prompt = PromptTemplate(
            agent_name=agent_name,
            prompt_type=prompt_type,
            group_id=group_id,
            content=content,
            version=new_version,
            is_active=activate,
            created_by=created_by,
            change_notes=change_notes,
            source_file=source_file,
            description=description,
        )
        self.db.add(prompt)

        logger.info(
            f"Created prompt version: {agent_name}:{prompt_type}:{group_id or 'base'} "
            f"v{new_version} (active={activate})"
        )

        return prompt

    def activate_version(
        self,
        agent_name: str,
        version: int,
        prompt_type: str = "system",
        group_id: Optional[str] = None,
    ) -> PromptTemplate:
        """Activate a specific version and refresh cache.

        Args:
            agent_name: Catalog ID (e.g., 'pdf', 'gene', 'supervisor')
            version: Specific version number to activate
            prompt_type: e.g., 'system', 'group_rules' (default: 'system')
            group_id: NULL for base prompts, e.g., 'FB' for group rules

        Returns:
            The activated PromptTemplate

        Raises:
            ValueError: If the specified version doesn't exist
        """
        # Deactivate current active version (scoped)
        self._deactivate_current(agent_name, prompt_type, group_id)

        # Find and activate specified version
        query = self.db.query(PromptTemplate).filter(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
            PromptTemplate.version == version,
        )
        if group_id is None:
            query = query.filter(PromptTemplate.group_id.is_(None))
        else:
            query = query.filter(PromptTemplate.group_id == group_id)

        prompt = query.first()

        if not prompt:
            raise ValueError(
                f"Version {version} not found for "
                f"{agent_name}/{prompt_type}/{group_id or 'base'}"
            )

        prompt.is_active = True
        self.db.commit()

        # Refresh cache after DB commit
        refresh_cache(self.db)

        logger.info(
            f"Activated prompt version: {agent_name}:{prompt_type}:{group_id or 'base'} v{version}"
        )

        return prompt

    def get_version_history(
        self,
        agent_name: str,
        prompt_type: str = "system",
        group_id: Optional[str] = None,
    ) -> List[PromptTemplate]:
        """Get all versions of a prompt, ordered by version descending.

        Args:
            agent_name: Catalog ID
            prompt_type: e.g., 'system', 'group_rules'
            group_id: NULL for base prompts, group ID for group rules

        Returns:
            List of PromptTemplate objects, newest first
        """
        query = self.db.query(PromptTemplate).filter(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
        )
        if group_id is None:
            query = query.filter(PromptTemplate.group_id.is_(None))
        else:
            query = query.filter(PromptTemplate.group_id == group_id)

        return query.order_by(PromptTemplate.version.desc()).all()

    def _deactivate_current(
        self,
        agent_name: str,
        prompt_type: str,
        group_id: Optional[str],
    ) -> None:
        """Deactivate the current active version for a prompt scope."""
        query = self.db.query(PromptTemplate).filter(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
            PromptTemplate.is_active == True,  # noqa: E712
        )
        if group_id is None:
            query = query.filter(PromptTemplate.group_id.is_(None))
        else:
            query = query.filter(PromptTemplate.group_id == group_id)

        query.update({"is_active": False})
