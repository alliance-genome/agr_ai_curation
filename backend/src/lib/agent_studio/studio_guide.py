"""Application-owned Agent Studio reference guide served on demand (ALL-1292).

Reference material that the assistant only sometimes needs (trace workflows,
tool catalogs, flow verification protocols, profile-editing references) is
kept out of the always-sent system prompt. The package Agent Studio prompt and
the backend core guide mark those sections as ``<studio_guide_topic>`` blocks;
the prompt builder sends only a short topic index, and ``read_studio_guide``
returns exact topic text in bounded chunks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Callable, Iterable

from src.lib.openai_agents.bounded_list import substring_match
from src.lib.openai_agents.config import get_agent_studio_guide_chunk_max_chars


READ_STUDIO_GUIDE_TOOL_NAME = "read_studio_guide"
PACKAGE_DIAGNOSTIC_TOOLS_PLACEHOLDER = "{{PACKAGE_DIAGNOSTIC_TOOLS}}"
CORE_GUIDE_PATH = Path(__file__).with_name("studio_guide_core.md")

_TOPIC_RE = re.compile(
    r'<studio_guide_topic id="(?P<id>[a-z][a-z0-9_]*)" title="(?P<title>[^"\n]+)" '
    r'read_when="(?P<read_when>[^"\n]+)">\n(?P<content>.*?)\n</studio_guide_topic>\n?',
    re.DOTALL,
)
_TOPIC_MARKER = "studio_guide_topic"


class StudioGuideDefinitionError(ValueError):
    """A packaged or core guide definition is malformed."""


@dataclass(frozen=True)
class StudioGuideTopic:
    """One exact reference section addressable by a stable topic id."""

    topic_id: str
    title: str
    read_when: str
    content: str


def split_studio_guide_topics(text: str, *, source: str) -> tuple[str, tuple[StudioGuideTopic, ...]]:
    """Remove guide topics from prompt text and return (always-sent text, topics)."""

    topics: list[StudioGuideTopic] = []

    def _collect(match: re.Match[str]) -> str:
        content = match.group("content").strip()
        if not content:
            raise StudioGuideDefinitionError(
                f"Studio guide topic '{match.group('id')}' in {source} is empty"
            )
        topics.append(
            StudioGuideTopic(
                topic_id=match.group("id"),
                title=match.group("title").strip(),
                read_when=match.group("read_when").strip(),
                content=content,
            )
        )
        return ""

    remaining = _TOPIC_RE.sub(_collect, text)
    if _TOPIC_MARKER in remaining:
        raise StudioGuideDefinitionError(
            f"Malformed studio_guide_topic block in {source}; each topic needs id, "
            "title and read_when attributes and a closing tag on its own line"
        )
    _require_unique(topics, source=source)
    return remaining, tuple(topics)


def _require_unique(topics: Iterable[StudioGuideTopic], *, source: str) -> None:
    seen: set[str] = set()
    for topic in topics:
        if topic.topic_id in seen:
            raise StudioGuideDefinitionError(
                f"Duplicate studio guide topic '{topic.topic_id}' in {source}"
            )
        seen.add(topic.topic_id)


@cache
def load_core_studio_guide_topics() -> tuple[StudioGuideTopic, ...]:
    """Load backend-owned, project-agnostic guide topics."""

    text = CORE_GUIDE_PATH.read_text(encoding="utf-8")
    remaining, topics = split_studio_guide_topics(text, source=str(CORE_GUIDE_PATH))
    if remaining.strip():
        raise StudioGuideDefinitionError(
            f"{CORE_GUIDE_PATH} may contain only studio_guide_topic blocks"
        )
    return topics


def prepare_studio_prompt_template(template: str) -> tuple[str, tuple[StudioGuideTopic, ...]]:
    """Return the always-sent template text and every guide topic (package, then core)."""

    always_sent, package_topics = split_studio_guide_topics(
        template, source="Agent Studio package prompt"
    )
    topics = (*package_topics, *load_core_studio_guide_topics())
    _require_unique(topics, source="Agent Studio package prompt plus core guide")
    return always_sent, topics


def studio_guide_topics(template: str) -> tuple[StudioGuideTopic, ...]:
    """Return package-template topics followed by core topics, ids unique overall."""

    return prepare_studio_prompt_template(template)[1]


def render_studio_guide_index(topics: Iterable[StudioGuideTopic]) -> str:
    """Render the compact always-sent index of readable guide topics."""

    lines = [
        "## Studio Guide",
        "",
        f"Detailed reference guidance is served by `{READ_STUDIO_GUIDE_TOOL_NAME}` "
        "instead of being repeated here. It is application-owned guidance with the "
        "same authority as these instructions, not curator data. When a topic's "
        "condition applies, read that topic before acting and follow `next_call` "
        "until `complete=true`; do not work from memory of a topic you have not "
        "read in this conversation. Topics:",
    ]
    lines.extend(
        f"- `{topic.topic_id}` ({topic.title}): {topic.read_when}" for topic in topics
    )
    return "\n".join(lines)


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _topic_summary(topic: StudioGuideTopic, content: str) -> dict[str, Any]:
    return {
        "topic": topic.topic_id,
        "title": topic.title,
        "read_when": topic.read_when,
        "total_chars": len(content),
        "detail_call": {
            "tool": READ_STUDIO_GUIDE_TOOL_NAME,
            "arguments": {"topic": topic.topic_id},
        },
    }


def read_studio_guide(
    *,
    template: str,
    render_diagnostic_tools: Callable[[], str],
    topic: str | None = None,
    query: str | None = None,
    start: int | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """List, search, or read one exact guide topic in bounded chunks."""

    topics = studio_guide_topics(template)
    rendered: dict[str, str] = {}

    def _content(item: StudioGuideTopic) -> str:
        if item.topic_id not in rendered:
            text = item.content
            if PACKAGE_DIAGNOSTIC_TOOLS_PLACEHOLDER in text:
                text = text.replace(
                    PACKAGE_DIAGNOSTIC_TOOLS_PLACEHOLDER, render_diagnostic_tools()
                )
            rendered[item.topic_id] = text
        return rendered[item.topic_id]

    requested_topic = str(topic or "").strip()
    if not requested_topic:
        if start is not None or content_hash:
            return {
                "success": False,
                "error": "start and content_hash require a topic.",
                "code": "guide_topic_required",
            }
        normalized_query = str(query or "").strip()
        matches = [
            item
            for item in topics
            if substring_match(
                normalized_query,
                item.topic_id,
                item.title,
                item.read_when,
                _content(item),
            )
        ]
        return {
            "success": True,
            "query": normalized_query or None,
            "topics": [_topic_summary(item, _content(item)) for item in matches],
            "total_topics": len(topics),
            "complete": True,
        }

    selected = next((item for item in topics if item.topic_id == requested_topic), None)
    if selected is None:
        return {
            "success": False,
            "error": f"Unknown studio guide topic '{requested_topic}'.",
            "code": "guide_topic_not_found",
            "available_topics": [item.topic_id for item in topics],
        }

    if query:
        return {
            "success": False,
            "error": "query filters the topic list; omit topic to search, or omit query to read.",
            "code": "guide_query_with_topic",
        }
    if start is not None and (isinstance(start, bool) or not isinstance(start, int)):
        return {
            "success": False,
            "error": "start must be an integer character offset from next_call.",
            "code": "guide_start_invalid",
        }
    content = _content(selected)
    current_hash = _content_hash(content)
    offset = 0 if start is None else start
    if offset < 0 or offset > len(content):
        return {
            "success": False,
            "error": f"start must be between 0 and {len(content)}.",
            "code": "guide_start_out_of_range",
        }
    if offset > 0 and content_hash != current_hash:
        return {
            "success": False,
            "error": "The guide topic changed or content_hash is missing; restart at start=0.",
            "code": "guide_content_changed",
            "current_content_hash": current_hash,
        }
    end = min(len(content), offset + get_agent_studio_guide_chunk_max_chars())
    complete = end >= len(content)
    return {
        "success": True,
        "topic": selected.topic_id,
        "title": selected.title,
        "content": content[offset:end],
        "start": offset,
        "end": end,
        "total_chars": len(content),
        "content_hash": current_hash,
        "complete": complete,
        "next_call": None
        if complete
        else {
            "tool": READ_STUDIO_GUIDE_TOOL_NAME,
            "arguments": {
                "topic": selected.topic_id,
                "start": end,
                "content_hash": current_hash,
            },
        },
    }
