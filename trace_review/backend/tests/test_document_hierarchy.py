"""The paper outline in agent instructions, listed file by file (0.12.0)."""

import unittest

from src.analyzers.document_hierarchy import DocumentHierarchyAnalyzer

OUTLINE = """You are helping the user with the document: "paper.pdf"

## Document Structure

This paper has 2 files. Each file lists its sections and subsections with their exact names; use these names, written exactly, with `read_section` and `read_subsection`.

File 1 of 2: paper.pdf (main paper, PDF, 42 chunks, chunks 0 to 41)
**Materials and Methods** (p.2-4, 3 chunks)
  └─ Fly strains (p.3, 1 chunks)
**Results** (p.5, 2 chunks)

File 2 of 2: Table S1 (raw).xlsx (supplement, Excel, 19 chunks, chunks 42 to 60)
**Sheet1** (19 chunks)

### How to use this outline:
- Use `read_section("<a section name from the outline>")` to read an entire top-level section
"""


# The outline as agents received it before 0.12.0 (format exactly as main rendered it; names made up).
OLD_OUTLINE = """You are helping the user with the document: "x.pdf"

## Document Structure

This document has the following hierarchical structure:

**TITLE** (p.1, 1 chunks)
**Introduction** (p.1, 5 chunks)
**Results and Discussion** (p.2-8, 25 chunks)
  \u2514\u2500 2.1. Design of the study (p.2, 3 chunks)
  \u2514\u2500 2.2. Made-up protein ratios (p.2-4, 3 chunks)
**Methods** (p.8-9, 7 chunks)

**Top-level sections (in order):** TITLE, Introduction, Results and Discussion, Methods

### How to use this hierarchy:
- Use `read_section("Methods")` to read an entire top-level section
"""


def _observations(instructions):
    return [{"type": "GENERATION", "metadata": {
        "instructions": instructions, "tools": [{"name": "read_section"}]}}]


class PerFileOutlineTests(unittest.TestCase):
    def test_each_file_is_parsed_with_its_own_sections(self):
        result = DocumentHierarchyAnalyzer.analyze({}, _observations(OUTLINE))

        self.assertTrue(result["found"])
        self.assertEqual(result["structure_type"], "hierarchy")
        self.assertEqual(
            [(f["number"], f["name"], f["role"], f["kind"], f["chunk_count"], f["chunk_index_start"],
              f["chunk_index_end"]) for f in result["files"]],
            [(1, "paper.pdf", "main paper", "PDF", 42, 0, 41),
             (2, "Table S1 (raw).xlsx", "supplement", "Excel", 19, 42, 60)],
        )
        self.assertEqual([s["name"] for s in result["files"][0]["sections"]], ["Materials and Methods", "Results"])
        self.assertEqual(result["files"][0]["sections"][0]["subsections"][0]["name"], "Fly strains")
        self.assertEqual(result["files"][1]["sections"], [
            {"name": "Sheet1", "page_range": "", "chunk_count": 19, "subsections": []}])
        self.assertEqual(result["top_level_sections"], ["Materials and Methods", "Results", "Sheet1"])
        self.assertEqual(result["chunk_count_total"], 3 + 1 + 2 + 19)

    def test_the_files_being_added_line_is_reported(self):
        adding = OUTLINE.replace(
            "**Sheet1** (19 chunks)\n",
            "**Sheet1** (19 chunks)\n\nFiles are still being added to this paper; they cannot be read yet.\n")

        result = DocumentHierarchyAnalyzer.analyze({}, _observations(adding))

        self.assertTrue(result["files_being_added"])
        self.assertEqual([s["name"] for s in result["files"][1]["sections"]], ["Sheet1"])
        self.assertFalse(DocumentHierarchyAnalyzer.analyze({}, _observations(OUTLINE))["files_being_added"])

    def test_the_abc_wait_line_is_reported(self):
        waiting = OUTLINE.replace(
            "**Sheet1** (19 chunks)\n",
            "**Sheet1** (19 chunks)\n\nABC has not finished converting this paper's supplements; they cannot be read yet.\n")

        result = DocumentHierarchyAnalyzer.analyze({}, _observations(waiting))

        self.assertTrue(result["supplements_converting"])
        self.assertFalse(result["files_being_added"])
        self.assertEqual([s["name"] for s in result["files"][1]["sections"]], ["Sheet1"])
        self.assertFalse(DocumentHierarchyAnalyzer.analyze({}, _observations(OUTLINE))["supplements_converting"])
        self.assertFalse(DocumentHierarchyAnalyzer.analyze({}, [])["supplements_converting"])

    def test_an_outline_recorded_before_the_per_file_outline_still_reads(self):
        result = DocumentHierarchyAnalyzer.analyze({}, _observations(OLD_OUTLINE))

        self.assertTrue(result["found"])
        self.assertEqual(result["structure_type"], "hierarchy")
        self.assertEqual([s["name"] for s in result["sections"]],
                         ["TITLE", "Introduction", "Results and Discussion", "Methods"])
        self.assertEqual([s["name"] for s in result["sections"][2]["subsections"]],
                         ["2.1. Design of the study", "2.2. Made-up protein ratios"])
        self.assertEqual(result["top_level_sections"], ["TITLE", "Introduction", "Results and Discussion", "Methods"])
        self.assertEqual(result["chunk_count_total"], 1 + 5 + 25 + 3 + 3 + 7)
        self.assertEqual(len(result["files"]), 1)
        self.assertIsNone(result["files"][0]["name"])
        self.assertFalse(result["files_being_added"])

    def test_instructions_without_an_outline_are_not_found(self):
        result = DocumentHierarchyAnalyzer.analyze({}, _observations("No document here."))

        self.assertFalse(result["found"])
        self.assertEqual(result["files"], [])
        self.assertFalse(DocumentHierarchyAnalyzer.analyze({}, [])["files_being_added"])


if __name__ == "__main__":
    unittest.main()
