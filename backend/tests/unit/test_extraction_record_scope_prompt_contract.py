"""Records answer the curator's question; contrasts go in the written answer (ALL-1501).

What becomes a record is the model's judgment. These tests pin only short anchors
of the prompt guidance that frames it, in the General PDF Extraction prompt and
both supervisor prompts; the Phase C inventories pin the full sentences. No code
reads record labels or paper text to decide scope.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PDF_PROMPT = REPO_ROOT / "packages/alliance/agents/pdf/prompt.yaml"
CONFIG_SUPERVISOR_PROMPT = REPO_ROOT / "config/agents/supervisor/prompt.yaml"
CORE_SUPERVISOR_PROMPT = REPO_ROOT / "packages/core/agents/supervisor/prompt.yaml"


def _content(path: Path) -> str:
    """Prompt content with line wrapping collapsed, so anchors match across lines."""

    content = str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])
    return re.sub(r"\s+", " ", content)


def test_pdf_prompt_keeps_records_to_the_question_and_contrasts_in_the_answer():
    content = _content(PDF_PROMPT)

    assert "Stage an object only for an item that answers the curator's question" in content
    # The paper need not use the curator's word for the category.
    assert "without using the curator's words" in content
    # Look-alikes are defined relative to the requested category, not as a fixed list.
    assert "controls that fall outside that category" in content
    assert "staged even when the paper uses it as a control" in content
    # Uncertain membership, not weak evidence in general, goes to the explanation.
    assert "membership in the requested category the paper does not establish" in content
    assert "asks for that explanation, not for more objects" in content
    assert "explain what your answer includes or leaves out" in content
    assert "the curator's own request asks for those items themselves" in content


def test_pdf_prompt_expects_exclusions_in_the_written_answer():
    content = _content(PDF_PROMPT)

    assert "Your final chat response is your written answer" in content
    assert "it is not curation output and needs no staged object" in content
    # The old wording read as forbidding the written answer from carrying exclusions.
    assert "or your final chat text" not in content


def test_both_supervisor_copies_keep_look_alikes_out_of_records():
    for path in (CONFIG_SUPERVISOR_PROMPT, CORE_SUPERVISOR_PROMPT):
        content = _content(path)
        assert "distinguish the requested category from look-alikes outside it" in content, path
        assert "items inside the category, controls included, stay records" in content, path
        assert "Adds no conditional fallback" in content, path
        assert '<example name="category_handoff_with_look_alikes">' in content, path


def test_config_supervisor_pdf_example_keeps_transgenic_controls_as_records():
    content = _content(CONFIG_SUPERVISOR_PROMPT)
    example = content.split('<example name="category_handoff_with_look_alikes">', 1)[1].split("</example>", 1)[0]

    assert "ask_pdf_extraction_specialist(" in example
    assert "including transgenic lines used as controls" in example
    assert "non-transgenic controls and mutants only in your written explanation" in example
    assert "if none" not in example.lower()
