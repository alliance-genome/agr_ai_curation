"""Records answer the curator's question; contrasts go in the written answer (ALL-1501).

What becomes a record is the model's judgment. These tests pin only the prompt
guidance that frames it: the General PDF Extraction prompt and both supervisor
prompts. No code reads record labels or paper text to decide scope.
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
    """Prompt content with line wrapping collapsed, so phrases match across lines."""

    content = str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])
    return re.sub(r"\s+", " ", content)


def test_pdf_prompt_stages_only_items_that_answer_the_question():
    content = _content(PDF_PROMPT)

    assert (
        "Stage an object only for an item that answers the curator's question, and only when "
        "the paper's evidence supports that it does"
    ) in content
    # The paper need not use the curator's word for the category.
    assert "the paper's own notation or description can show this without using the curator's words" in content
    assert (
        "Items you contrast with or exclude from the requested category, such as controls, "
        "wild-type strains, or look-alikes outside that category."
    ) in content
    assert "Relevant items the paper does not establish as answering the question, with your uncertainty." in content


def test_pdf_prompt_treats_a_request_to_distinguish_as_a_request_for_explanation():
    content = _content(PDF_PROMPT)

    assert (
        "A request to distinguish, contrast, or clarify, whether it comes from the curator or from "
        "the supervisor's delegation, asks for that explanation, not for more objects."
    ) in content
    assert "Do not stage a claim whose only purpose is to clarify scope or list exclusions." in content
    # The curator can still ask for the contrasted items themselves.
    assert (
        "Stage contrasted or excluded items only when the curator's own request asks for those "
        "items themselves, not only for the distinction."
    ) in content


def test_pdf_prompt_expects_exclusions_in_the_written_answer():
    content = _content(PDF_PROMPT)

    assert "Your final chat response is your written answer" in content
    assert "name the exclusions, contrasts, and uncertainty there" in content
    assert "That explanation is expected; it is not curation output and needs no staged object." in content
    assert "Curation output comes only from finalized staged objects" in content
    # The old wording read as forbidding the written answer from carrying exclusions.
    assert "or your final chat text" not in content
    assert "the staged objects match the curator's requested scope" not in content


def test_supervisor_handoffs_keep_look_alikes_out_of_records_in_both_copies():
    for path in (CONFIG_SUPERVISOR_PROMPT, CORE_SUPERVISOR_PROMPT):
        content = _content(path)
        assert "When asking a specialist to distinguish the requested category from look-alikes" in content, path
        assert "says the look-alikes are named only in its written explanation, not kept as records." in content, path
        assert 'such as "if none are found, list X"; an explained empty result is a valid answer.' in content, path
        assert '<example name="category_handoff_with_look_alikes">' in content, path
    assert "Adds no conditional fallback the curator did not ask for" in _content(CONFIG_SUPERVISOR_PROMPT)
    assert "Adds no conditional fallback the user did not ask for" in _content(CORE_SUPERVISOR_PROMPT)


def test_config_supervisor_has_a_pdf_extraction_handoff_example():
    content = _content(CONFIG_SUPERVISOR_PROMPT)
    example = content.split('<example name="category_handoff_with_look_alikes">', 1)[1].split("</example>", 1)[0]

    assert 'Query: "What transgenic flies are used in the publication?"' in example
    assert "ask_pdf_extraction_specialist(" in example
    assert "Keep as records only lines the paper's notation or description shows to be transgenic." in example
    assert (
        "Name wild-type strains, controls, and non-transgenic mutants only in your written "
        "explanation; do not keep them as records."
    ) in example
    # The example adds no conditional fallback list.
    assert "if none" not in example.lower()
