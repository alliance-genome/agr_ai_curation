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
