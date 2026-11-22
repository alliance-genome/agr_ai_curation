"""The sections stored on pieces of a three-level text paper are pinned (ALL-1460, P9a).

P9a factors the hierarchy apply step into one function the pipeline and the viewer's
text route share. The pieces' section fields must not change. This file pins them for
a made-up main text paper (title, numbered sections, subsections and a fourth-level
heading) cut by the real Markdown converter, hierarchy step and chunker, with the
classifier replaced by a fixed answer. It was run on main ``a88e06413`` before the
refactor and on the P9a branch after it, with the same result.
"""

from __future__ import annotations

import asyncio
import json

from src.lib.pipeline import hierarchy_resolution as hierarchy
from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.models.strategy import ChunkingStrategy

THREE_LEVEL_PAPER = """# Made-up paper on wing growth

## Abstract

Made-up flies grew made-up wings.

## 1 Introduction

Wings are made up of cells.

## 2 Materials and methods

### 2.1 Fly strains

Flies were raised on made-up food.

#### 2.1.1 Stock list

| Stock | Genotype |
|---|---|
| BL1 | made-up genotype |

### 2.2 Imaging

Discs were imaged with a made-up microscope.

## 3 Results

Clones lacking the made-up gene were small.
"""

METHODS = "2 Materials and methods"
CLASSIFIER_ANSWER = {
    "Made-up paper on wing growth": ("Made-up paper on wing growth", None),
    "Abstract": ("Abstract", None),
    "1 Introduction": ("1 Introduction", None),
    METHODS: (METHODS, None),
    "2.1 Fly strains": (METHODS, "2.1 Fly strains"),
    "2.1.1 Stock list": (METHODS, "2.1.1 Stock list"),
    "2.2 Imaging": (METHODS, "2.2 Imaging"),
    "3 Results": ("3 Results", None),
}


def fixed_classifier(monkeypatch):
    async def _answer(section_info):
        return (
            [
                hierarchy.SectionItem(
                    header=info["title"],
                    parent_section=CLASSIFIER_ANSWER[info["title"]][0],
                    subsection=CLASSIFIER_ANSWER[info["title"]][1],
                    is_top_level=CLASSIFIER_ANSWER[info["title"]][1] is None,
                )
                for info in section_info
            ],
            {"model": "made-up"},
        )

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _answer)
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "made-up-model")


def piece_sections(elements):
    chunks = asyncio.run(
        chunk_parsed_document(elements, ChunkingStrategy.get_research_strategy(), "made-up-document")
    )
    return [
        {
            "starts": chunk.content[:24],
            "section_title": chunk.section_title,
            "parent_section": chunk.parent_section,
            "subsection": chunk.subsection,
            "section_path": chunk.section_path,
        }
        for chunk in chunks
    ]


EXPECTED_PIECE_SECTIONS: list[dict] = [
    {
        "starts": 'Made-up paper on wing gr',
        "section_title": 'Made-up paper on wing growth',
        "parent_section": 'Made-up paper on wing growth',
        "subsection": None,
        "section_path": ['Made-up paper on wing growth'],
    },
    {
        "starts": 'Abstract\n\nMade-up flies ',
        "section_title": 'Abstract',
        "parent_section": 'Abstract',
        "subsection": None,
        "section_path": ['Abstract'],
    },
    {
        "starts": '1 Introduction\n\nWings ar',
        "section_title": '1 Introduction',
        "parent_section": '1 Introduction',
        "subsection": None,
        "section_path": ['1 Introduction'],
    },
    {
        "starts": '2 Materials and methods\n',
        "section_title": '2 Materials and methods > 2.1 Fly strains',
        "parent_section": '2 Materials and methods',
        "subsection": '2.1 Fly strains',
        "section_path": ['2 Materials and methods', '2.1 Fly strains'],
    },
    {
        "starts": '2.1.1 Stock list\n\n| Stoc',
        "section_title": '2 Materials and methods > 2.1.1 Stock list',
        "parent_section": '2 Materials and methods',
        "subsection": '2.1.1 Stock list',
        "section_path": ['2 Materials and methods', '2.1.1 Stock list'],
    },
    {
        "starts": '2.2 Imaging\n\nDiscs were ',
        "section_title": '2 Materials and methods > 2.2 Imaging',
        "parent_section": '2 Materials and methods',
        "subsection": '2.2 Imaging',
        "section_path": ['2 Materials and methods', '2.2 Imaging'],
    },
    {
        "starts": '3 Results\n\nClones lackin',
        "section_title": '3 Results',
        "parent_section": '3 Results',
        "subsection": None,
        "section_path": ['3 Results'],
    },
]


def test_stored_piece_sections_of_a_three_level_paper_are_unchanged(monkeypatch):
    fixed_classifier(monkeypatch)
    elements = markdown_to_pipeline_elements(THREE_LEVEL_PAPER)

    resolved, metadata = asyncio.run(hierarchy.resolve_document_hierarchy(elements))
    actual = piece_sections(resolved)

    assert metadata is not None
    assert actual == EXPECTED_PIECE_SECTIONS, json.dumps(actual)
