from src.lib.document_sources.abc_literature_selection import (
    AbcConvertedMarkdownDecisionStatus,
    AbcReferenceFileCandidate,
    select_converted_main_markdown,
)


def _source_pdf(
    *,
    referencefile_id: int = 10,
    display_name: str = "paper",
    open_access: bool = False,
    mod_abbreviations: tuple[str | None, ...] = (),
) -> AbcReferenceFileCandidate:
    return AbcReferenceFileCandidate(
        referencefile_id=referencefile_id,
        display_name=display_name,
        file_class="main",
        file_extension="pdf",
        file_publication_status="final",
        open_access=open_access,
        mod_abbreviations=mod_abbreviations,
    )


def _converted(
    *,
    referencefile_id: int = 20,
    display_name: str = "paper_merged",
    file_class: str = "converted_merged_main",
    file_extension: str = "md",
    status: str = "final",
    mod_abbreviations: tuple[str | None, ...] = (),
) -> AbcReferenceFileCandidate:
    return AbcReferenceFileCandidate(
        referencefile_id=referencefile_id,
        display_name=display_name,
        file_class=file_class,
        file_extension=file_extension,
        file_publication_status=status,
        mod_abbreviations=mod_abbreviations,
    )


def test_selects_global_source_pdf_without_mod_membership():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(mod_abbreviations=(None,))],
        converted_files=[_converted()],
        authorized_mod_abbreviations=[],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.READY
    assert decision.converted_markdown is not None
    assert decision.converted_markdown.referencefile_id == 20


def test_selects_mod_scoped_source_pdf_for_authorized_curator():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(mod_abbreviations=("FB",))],
        converted_files=[_converted()],
        authorized_mod_abbreviations=["FB"],
    )

    assert decision.ready is True
    assert decision.source_pdf is not None
    assert decision.source_pdf.mod_abbreviations == ("FB",)


def test_null_mod_on_converted_row_is_not_an_access_grant():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(mod_abbreviations=("FB",))],
        converted_files=[_converted(mod_abbreviations=(None,))],
        authorized_mod_abbreviations=["WB"],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.NO_AUTHORIZED_SOURCE_PDF
    assert decision.converted_markdown is None


def test_reports_no_converted_main_markdown_when_only_supplement_exists():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(open_access=True)],
        converted_files=[_converted(file_class="converted_merged_supplement")],
        authorized_mod_abbreviations=[],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.NO_CONVERTED_MAIN_MARKDOWN
    assert decision.source_pdf is not None


def test_reports_tei_only_without_selecting_tei_derived_markdown():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(open_access=True)],
        converted_files=[_converted(display_name="paper_tei")],
        authorized_mod_abbreviations=[],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.TEI_ONLY
    assert decision.converted_markdown is None


def test_multiple_converted_candidates_use_deterministic_preference_order():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(open_access=True)],
        converted_files=[
            _converted(referencefile_id=30, display_name="paper_marker"),
            _converted(referencefile_id=40, display_name="paper_nxml"),
            _converted(referencefile_id=50, display_name="paper_merged"),
            _converted(referencefile_id=60, display_name="paper_tei"),
        ],
        authorized_mod_abbreviations=[],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.READY
    assert decision.converted_markdown is not None
    assert decision.converted_markdown.referencefile_id == 50


def test_multiple_same_suffix_candidates_choose_highest_referencefile_id():
    decision = select_converted_main_markdown(
        source_files=[_source_pdf(open_access=True)],
        converted_files=[
            _converted(referencefile_id=50, display_name="paper_merged"),
            _converted(referencefile_id=51, display_name="paper_merged"),
        ],
        authorized_mod_abbreviations=[],
    )

    assert decision.status == AbcConvertedMarkdownDecisionStatus.READY
    assert decision.converted_markdown is not None
    assert decision.converted_markdown.referencefile_id == 51

