"""Per-file structural checks R1 to R7: one failing case per check and its passing twin."""

from __future__ import annotations

import pytest

from src.lib.document_sources.file_assessment import (
    FileCheckLimits,
    FileCheckReason,
    FileProfile,
    assess_converted_file,
)


def _limits(**overrides) -> FileCheckLimits:
    values = dict(
        max_bytes=10_000,
        few_characters_below=20,
        row_token_limit=50,
        # One token per character keeps the arithmetic visible in the tests.
        count_tokens=len,
    )
    values.update(overrides)
    return FileCheckLimits(**values)


PLAIN = FileProfile(single_title_required=False)
TITLED = FileProfile(single_title_required=True)

TABLE = (
    "# Made-up workbook\n\n"
    "Sheet one lists made-up strains and their scores.\n\n"
    "| strain | score |\n"
    "| --- | --- |\n"
    "| s1 | 1 |\n"
    "| s2 | 2 |\n"
)


def _assess(text: str, profile: FileProfile = PLAIN, **limits) -> object:
    return assess_converted_file(text.encode("utf-8"), profile, _limits(**limits))


def test_a_well_formed_file_passes_with_no_note():
    result = _assess(TABLE, TITLED)
    assert result.refused is False and result.reason is None
    assert result.few_characters_count is None


def test_r1_too_large_is_refused_and_the_limit_passes():
    content = "x" * 101
    assert _assess(content, max_bytes=100).reason is FileCheckReason.TOO_LARGE
    refused = _assess(content, max_bytes=100)
    assert (refused.measured_value, refused.limit_value) == (101, 100)
    assert _assess("x" * 100, max_bytes=100, few_characters_below=0).refused is False


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe not utf-8", "bad � char".encode(), "nul \x00 char".encode(), "bell \x07".encode(),
     "del \x7f".encode()],
)
def test_r2_damaged_or_control_characters_are_refused(content):
    result = assess_converted_file(content, PLAIN, _limits())
    assert result.reason is FileCheckReason.INVALID_TEXT


def test_r2_tabs_and_both_line_endings_pass():
    assert _assess("a\tb\r\nc\nmore readable text here", few_characters_below=0).refused is False


def test_r3_few_characters_is_a_note_only_with_the_count():
    result = _assess("# Title\n\n![figure](image.png)\n", few_characters_below=200)
    assert result.refused is False
    assert result.few_characters_count == len("![figure](image.png)")


def test_r3_does_not_count_headings_pipes_or_separator_rows():
    text = "# Heading words\n\n| a | b |\n| --- | --- |\n| cd | e |\n"
    assert _assess(text, few_characters_below=100).few_characters_count == len("abcde")


def test_r4_table_without_a_separator_line_is_refused():
    text = "| strain | score |\n| s1 | 1 |\n"
    assert _assess(text).reason is FileCheckReason.TABLE_CELLS_MISMATCH


def test_r4_row_with_a_different_number_of_cells_is_refused():
    text = TABLE + "| s3 | 3 | extra |\n"
    result = _assess(text)
    assert result.reason is FileCheckReason.TABLE_CELLS_MISMATCH
    assert (result.measured_value, result.limit_value) == (3, 2)


def test_r4_an_escaped_pipe_inside_a_cell_is_not_a_cell_boundary():
    text = TABLE + "| a \\| b | 4 |\n"
    assert _assess(text).refused is False


def test_r5_a_row_over_the_token_limit_is_refused():
    wide = "| " + "w" * 60 + " | 1 |"
    text = TABLE + wide + "\n"
    result = _assess(text, row_token_limit=50)
    assert result.reason is FileCheckReason.TABLE_ROW_TOO_WIDE
    assert result.measured_value == len(wide) and result.limit_value == 50
    assert _assess(text, row_token_limit=len(wide)).refused is False


def test_r6_a_separator_row_inside_the_data_is_refused():
    text = TABLE + "| --- | --- |\n| s4 | 4 |\n"
    assert _assess(text).reason is FileCheckReason.TABLE_STRAY_SEPARATOR


@pytest.mark.parametrize("titles", [0, 2])
def test_r7_needs_exactly_one_top_level_title_when_the_profile_asks(titles):
    text = "".join(f"# Title {n}\n\n" for n in range(titles)) + "## Sheet\n\nsome readable text\n"
    result = _assess(text, TITLED, few_characters_below=0)
    assert result.reason is FileCheckReason.TITLE_COUNT
    assert (result.measured_value, result.limit_value) == (titles, 1)
    assert _assess(text, PLAIN, few_characters_below=0).refused is False


def test_check_version_changes_with_every_threshold():
    base = _limits().check_version
    assert _limits(max_bytes=10_001).check_version != base
    assert _limits(few_characters_below=21).check_version != base
    assert _limits(row_token_limit=51).check_version != base
    assert _limits(count_tokens=lambda text: 0).check_version == base
