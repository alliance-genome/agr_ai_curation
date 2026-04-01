"""Unit tests for RapidFuzz PDF evidence localization."""

from src.lib.pdf_viewer.rapidfuzz_matcher import PdfPageText, match_quote_to_pdf_pages


def test_match_quote_to_pdf_pages_localizes_single_page_quote():
    result = match_quote_to_pdf_pages(
        "Perturbing Crb affects rhabdomere morphogenesis and eventually leads to retinal degeneration.",
        [
            PdfPageText(page_number=1, raw_text="Introduction and background."),
            PdfPageText(
                page_number=2,
                raw_text=(
                    "Results. Perturbing Crb affects rhabdomere morphogenesis and, eventually, "
                    "leads to retinal degeneration. However, how the rhabdomere proteins change remains unclear."
                ),
            ),
            PdfPageText(page_number=3, raw_text="Discussion."),
        ],
    )

    assert result.found is True
    assert result.strategy == "rapidfuzz-single-page"
    assert result.matched_page == 2
    assert result.matched_range is not None
    assert "Perturbing Crb affects rhabdomere morphogenesis" in (result.matched_query or "")
    assert result.cross_page is False


def test_match_quote_to_pdf_pages_uses_page_hints_as_tie_breaker():
    duplicated_quote = "The same evidence sentence appears in multiple places."

    result = match_quote_to_pdf_pages(
        duplicated_quote,
        [
            PdfPageText(page_number=2, raw_text=f"Results. {duplicated_quote} More text."),
            PdfPageText(page_number=3, raw_text=f"Methods. {duplicated_quote} More text."),
        ],
        page_hints=[3],
    )

    assert result.found is True
    assert result.matched_page == 3
    assert result.matched_range is not None
    assert result.matched_range.page_number == 3


def test_match_quote_to_pdf_pages_supports_cross_page_spans():
    result = match_quote_to_pdf_pages(
        "all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton",
        [
            PdfPageText(page_number=1, raw_text="Introduction"),
            PdfPageText(
                page_number=2,
                raw_text="Results. all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates",
            ),
            PdfPageText(
                page_number=3,
                raw_text="in the connection of the Crumbs function in organizing the cytoskeleton and should be prioritized.",
            ),
        ],
        page_hints=[2],
    )

    assert result.found is True
    assert result.cross_page is True
    assert result.matched_page == 2
    assert len(result.page_ranges) == 2
    assert result.page_ranges[0].page_number == 2
    assert result.page_ranges[1].page_number == 3


def test_match_quote_to_pdf_pages_rejects_low_scoring_candidates():
    result = match_quote_to_pdf_pages(
        "completely unrelated quote text",
        [
            PdfPageText(page_number=1, raw_text="Introduction and background"),
            PdfPageText(page_number=2, raw_text="Methods and materials"),
        ],
        min_score=99.0,
    )

    assert result.found is False
    assert result.matched_page is not None
    assert result.score < 99.0
