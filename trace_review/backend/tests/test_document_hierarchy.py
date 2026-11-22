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

    def test_instructions_without_an_outline_are_not_found(self):
        result = DocumentHierarchyAnalyzer.analyze({}, _observations("No document here."))

        self.assertFalse(result["found"])
        self.assertEqual(result["files"], [])


if __name__ == "__main__":
    unittest.main()
