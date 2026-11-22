"""
Document Hierarchy Analyzer
Extracts document section hierarchy from PDF specialist agent instructions.

This analyzer parses the injected hierarchy information from PDF agent prompts
to show how the document was structured for search/retrieval.
"""
import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class DocumentHierarchyAnalyzer:
    """Analyzes document hierarchy from PDF specialist agent context"""

    @classmethod
    def analyze(cls, trace: Dict, observations: List[Dict]) -> Dict:
        """
        Extract document hierarchy from trace data.

        Args:
            trace: Complete trace data from Langfuse (raw_trace)
            observations: List of all observations

        Returns:
            Dictionary with:
            - found: Whether hierarchy info was found
            - document_name: Name of the document
            - structure_type: "hierarchy" or "flat" or "unknown"
            - top_level_sections: List of top-level section names
            - sections: Detailed section breakdown with subsections
            - raw_hierarchy_text: The raw hierarchy text from instructions
        """
        # Extract raw_trace if wrapped
        raw_trace = trace.get("raw_trace", trace)

        # Find PDF specialist generation to extract instructions
        pdf_instructions = cls._find_pdf_specialist_instructions(observations)

        if not pdf_instructions:
            return {
                "found": False,
                "document_name": None,
                "structure_type": "unknown",
                "top_level_sections": [],
                "sections": [],
                "raw_hierarchy_text": None,
                "error": "No PDF specialist found in trace"
            }

        # Extract document name
        document_name = cls._extract_document_name(pdf_instructions)

        # Parse hierarchy from instructions
        hierarchy_info = cls._parse_hierarchy_from_instructions(pdf_instructions)

        return {
            "found": hierarchy_info.get("found", False),
            "document_name": document_name,
            "structure_type": hierarchy_info.get("structure_type", "unknown"),
            "top_level_sections": hierarchy_info.get("top_level_sections", []),
            "sections": hierarchy_info.get("sections", []),
            "files": hierarchy_info.get("files", []),
            "raw_hierarchy_text": hierarchy_info.get("raw_text"),
            "chunk_count_total": hierarchy_info.get("chunk_count_total", 0)
        }

    @classmethod
    def _find_pdf_specialist_instructions(cls, observations: List[Dict]) -> Optional[str]:
        """Find and return PDF specialist instructions from observations.

        Uses two-pass strategy:
        1. First pass: Look for observations with PDF specialist tools (most reliable)
        2. Second pass: Fall back to checking instructions content
        """
        # First pass: Look for PDF specialist by tools (most reliable)
        for obs in observations:
            if obs.get("type") != "GENERATION":
                continue

            meta = obs.get("metadata") or {}
            instructions = meta.get("instructions", "")

            # Check for PDF specialist tools
            tools = meta.get("tools") or []
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

            # PDF specialist has search_document and read_section tools
            if "search_document" in tool_names or "read_section" in tool_names:
                return instructions

        # Second pass: Fall back to instructions content check
        for obs in observations:
            if obs.get("type") != "GENERATION":
                continue

            meta = obs.get("metadata") or {}
            instructions = meta.get("instructions", "")

            # Check for Document Structure section in instructions
            if instructions and "## Document Structure" in instructions:
                return instructions

        return None

    @classmethod
    def _extract_document_name(cls, instructions: str) -> Optional[str]:
        """Extract document name from instructions."""
        # Pattern: You are helping the user with the document: "filename.pdf"
        match = re.search(r'with the document:\s*"([^"]+)"', instructions)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _parse_hierarchy_from_instructions(cls, instructions: str) -> Dict:
        """Parse hierarchy information from PDF specialist instructions."""
        result = {
            "found": False,
            "structure_type": "unknown",
            "top_level_sections": [],
            "sections": [],
            "files": [],
            "raw_text": None,
            "chunk_count_total": 0
        }

        # The outline is listed file by file (0.12.0): "This paper has N files." then,
        # for each file, "File n of m: name (role, kind, k chunks, chunks a to b)"
        # followed by its section lines, up to "### How to use this outline:".
        hierarchy_match = re.search(
            r'## Document Structure\s*\n+This paper has [^\n]*\n+(.*?)(?=\n### How to use)',
            instructions,
            re.DOTALL
        )

        # Also try flat sections pattern
        flat_match = re.search(
            r'## Document Sections\s*\n+This document has the following sections available:\s*\n+(.*?)(?=Use this information|\n### )',
            instructions,
            re.DOTALL
        )

        if hierarchy_match:
            raw_text = hierarchy_match.group(1).strip()
            result["found"] = True
            result["raw_text"] = raw_text
            result["files"] = cls._parse_files(raw_text)
            result["sections"] = [section for file in result["files"] for section in file["sections"]]

            # Check for hierarchy resolution failure (only "Unknown" section)
            if len(result["sections"]) == 1 and result["sections"][0].get("name") == "Unknown":
                result["structure_type"] = "unresolved"
                result["resolution_failed"] = True
            else:
                result["structure_type"] = "hierarchy"
                result["resolution_failed"] = False
            result["top_level_sections"] = [section["name"] for section in result["sections"]]

        elif flat_match:
            raw_text = flat_match.group(1).strip()
            result["found"] = True
            result["structure_type"] = "flat"
            result["raw_text"] = raw_text
            result["sections"] = cls._parse_flat_sections(raw_text)
            result["top_level_sections"] = [s["name"] for s in result["sections"]]

        # Calculate total chunks
        for section in result.get("sections", []):
            result["chunk_count_total"] += section.get("chunk_count", 0)
            for sub in section.get("subsections", []):
                result["chunk_count_total"] += sub.get("chunk_count", 0)

        return result

    _FILE_LINE = re.compile(
        r'^File (\d+) of (\d+): (.+) \((main paper|supplement), ([^,]+), (\d+) chunks, chunks (\d+) to (\d+)\)$'
    )

    @classmethod
    def _parse_files(cls, raw_text: str) -> List[Dict]:
        """Split the outline into its files, each with its own sections."""
        files: List[Dict] = []
        lines: List[str] = []

        def close() -> None:
            if files:
                files[-1]["sections"] = cls._parse_hierarchical_sections("\n".join(lines))

        for line in raw_text.split("\n"):
            match = cls._FILE_LINE.match(line.strip())
            if match:
                close()
                lines = []
                files.append({
                    "number": int(match.group(1)),
                    "name": match.group(3),
                    "role": match.group(4),
                    "kind": match.group(5),
                    "chunk_count": int(match.group(6)),
                    "chunk_index_start": int(match.group(7)),
                    "chunk_index_end": int(match.group(8)),
                    "sections": [],
                })
            elif files:
                lines.append(line)
        close()
        return files

    @classmethod
    def _parse_hierarchical_sections(cls, raw_text: str) -> List[Dict]:
        """Parse hierarchical section structure from raw text."""
        sections = []
        current_section = None

        for line in raw_text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Top-level section: **SectionName** (p.X-Y, N chunks)
            top_match = re.match(r'\*\*(.+?)\*\*\s*\(([^)]+)\)', line)
            if top_match and not line.startswith("└"):
                if current_section:
                    sections.append(current_section)

                name = top_match.group(1).strip()
                meta = top_match.group(2)

                current_section = {
                    "name": name,
                    "page_range": cls._extract_page_range(meta),
                    "chunk_count": cls._extract_chunk_count(meta),
                    "subsections": []
                }
                continue

            # Subsection: └─ SubsectionName (p.X, N chunks)
            sub_match = re.match(r'[└├]─\s*(.+?)\s*\(([^)]+)\)', line)
            if sub_match and current_section:
                name = sub_match.group(1).strip()
                meta = sub_match.group(2)

                current_section["subsections"].append({
                    "name": name,
                    "page_range": cls._extract_page_range(meta),
                    "chunk_count": cls._extract_chunk_count(meta)
                })

        if current_section:
            sections.append(current_section)

        return sections

    @classmethod
    def _parse_flat_sections(cls, raw_text: str) -> List[Dict]:
        """Parse flat section list from raw text."""
        sections = []

        for line in raw_text.split("\n"):
            line = line.strip()
            if not line or not line.startswith("-"):
                continue

            # Pattern: - **SectionName** (p.X-Y, N chunks)
            match = re.match(r'-\s*\*\*(.+?)\*\*\s*\(([^)]+)\)', line)
            if match:
                name = match.group(1).strip()
                meta = match.group(2)

                sections.append({
                    "name": name,
                    "page_range": cls._extract_page_range(meta),
                    "chunk_count": cls._extract_chunk_count(meta),
                    "subsections": []
                })

        return sections

    @staticmethod
    def _extract_page_range(meta: str) -> str:
        """Extract page range from metadata string."""
        # Patterns: p.3, p.1-10
        match = re.search(r'p\.(\d+(?:-\d+)?)', meta)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_chunk_count(meta: str) -> int:
        """Extract chunk count from metadata string."""
        # Pattern: N chunks
        match = re.search(r'(\d+)\s*chunks?', meta)
        return int(match.group(1)) if match else 0
