"""The readable-files value every document read takes (KANBAN-1976 / ALL-1460, P8a)."""

import asyncio

import pytest

from src.lib.document_files.readable import (
    ReadableFile,
    ReadableFiles,
    ReadingScope,
    current_reading_scope,
    reading_scope,
)
from tests.readable_files_support import files


PAPER = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 42, 60), ("Data S2.docx", 70, 75))


@pytest.mark.parametrize("index, name", [
    (0, "paper.pdf"), (41, "paper.pdf"), (42, "Table S1.xlsx"), (60, "Table S1.xlsx"),
    (70, "Data S2.docx"), (75, "Data S2.docx"),
])
def test_a_piece_number_belongs_to_the_file_whose_range_holds_it(index, name):
    assert PAPER.contains(index)
    assert PAPER.file_for(index).display_name == name


@pytest.mark.parametrize("index", [-1, 61, 69, 76, True, "3", 4.0, None])
def test_a_piece_number_outside_every_range_or_not_an_integer_is_not_readable(index):
    assert not PAPER.contains(index)
    assert PAPER.file_for(index) is None


def test_the_ranges_read_as_today_for_one_file_and_name_each_file_for_several():
    one = ReadableFiles("doc-1", (ReadableFile("f", "paper.pdf", "main", 0, 348),))

    assert one.ranges_text() == "0 to 348"
    assert PAPER.ranges_text() == "0 to 41 (paper.pdf), 42 to 60 (Table S1.xlsx), 70 to 75 (Data S2.docx)"


def test_ranges_are_told_in_piece_order_whatever_the_tab_order():
    reordered = ReadableFiles("doc-1", (
        ReadableFile("b", "Table S1.xlsx", "supplement", 42, 60),
        ReadableFile("a", "paper.pdf", "main", 0, 41),
    ))

    assert reordered.ranges_text() == "0 to 41 (paper.pdf), 42 to 60 (Table S1.xlsx)"


def test_the_fingerprint_changes_when_a_file_is_added_or_removed():
    fewer = files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 42, 60))

    assert PAPER.fingerprint != fewer.fingerprint
    assert files("doc-1", ("paper.pdf", 0, 41), ("Table S1.xlsx", 42, 60)).fingerprint == fewer.fingerprint


def test_runs_read_every_ready_file_unless_their_scope_says_main_file_only():
    assert current_reading_scope() is ReadingScope.ALL_READY_FILES
    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        assert current_reading_scope() is ReadingScope.MAIN_FILE_ONLY
    assert current_reading_scope() is ReadingScope.ALL_READY_FILES
    with pytest.raises(TypeError):
        with reading_scope("main_file_only"):
            pass


def test_the_scope_reaches_tasks_and_threads_started_inside_it():
    async def read():
        return current_reading_scope()

    async def inside():
        thread_value = await asyncio.to_thread(current_reading_scope)
        task_value = await asyncio.create_task(read())
        return thread_value, task_value

    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        assert asyncio.run(inside()) == (ReadingScope.MAIN_FILE_ONLY, ReadingScope.MAIN_FILE_ONLY)


def test_files_sharing_a_name_get_numbered_labels_that_never_collide_with_a_real_name():
    readable = ReadableFiles("doc-1", (
        ReadableFile("a", "Table S1.xlsx", "supplement", 0, 1),
        ReadableFile("b", "Table S1.xlsx (2)", "supplement", 2, 3),
        ReadableFile("c", "Table S1.xlsx", "supplement", 4, 5),
        ReadableFile("d", "Table S1.xlsx", "supplement", 6, 7),
    ))

    assert [(label, file.file_id) for label, file in readable.labelled()] == [
        ("Table S1.xlsx", "a"), ("Table S1.xlsx (2)", "b"), ("Table S1.xlsx (3)", "c"), ("Table S1.xlsx (4)", "d"),
    ]
    assert readable.by_label("Table S1.xlsx (2)").file_id == "b"
    assert readable.by_label("Table S1.xlsx (3)").file_id == "c"
    assert readable.by_label("Table S1.xlsx (5)") is None
    assert readable.ranges_text() == (
        "0 to 1 (Table S1.xlsx), 2 to 3 (Table S1.xlsx (2)), 4 to 5 (Table S1.xlsx (3)), 6 to 7 (Table S1.xlsx (4))"
    )


def test_one_file_of_the_paper_can_be_read_on_its_own():
    sheet = PAPER.by_label("Table S1.xlsx")

    assert PAPER.only(sheet).files == (sheet,)
    with pytest.raises(Exception, match="not one of this paper's readable files"):
        PAPER.only(ReadableFile("x", "other.pdf", "supplement", 90, 99))


@pytest.mark.parametrize("display_mode, extension, kind", [
    ("local_pdf", None, "PDF"), ("provider_pdf_proxy", "pdf", "PDF"), ("text", "xlsx", "Excel"),
    ("text", ".DOCX", "Word"), ("text", "csv", "text"), ("text", None, "text"),
])
def test_a_files_kind_comes_from_its_display_mode_and_extension(display_mode, extension, kind):
    from src.lib.document_files.readable import file_kind

    assert file_kind(display_mode, extension) == kind
