"""The one stored-file delete helper stays inside the owner's folder and tries every path (P10b R2)."""

from __future__ import annotations

import pytest

from src.lib.document_files.service import StoredFilesNotRemoved, remove_stored_files
from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage

OWNER = "owner-subject"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path / "storage")
    root = tmp_path / "storage"
    for relative in (
        f"{OWNER}/source_markdown/a.md", f"{OWNER}/processed_json/a.json", f"{OWNER}/source_markdown/b.md",
        "other-subject/source_markdown/victim.md",
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text("made-up")
    (tmp_path / "outside.txt").write_text("made-up")
    return root


def test_only_the_named_files_go_and_a_missing_one_is_fine(storage):
    remove_stored_files(
        [f"{OWNER}/source_markdown/a.md", f"{OWNER}/processed_json/a.json", f"{OWNER}/gone.md"], owner_subject=OWNER,
    )
    assert not (storage / OWNER / "source_markdown/a.md").exists()
    assert not (storage / OWNER / "processed_json/a.json").exists()
    assert (storage / OWNER / "source_markdown/b.md").is_file()
    assert (storage / "other-subject/source_markdown/victim.md").is_file()


@pytest.mark.parametrize("escaping", [
    "../outside.txt",
    "{outside}",
    "other-subject/source_markdown/victim.md",
    f"{OWNER}/../other-subject/source_markdown/victim.md",
])
def test_a_path_outside_the_owners_folder_refuses_and_deletes_nothing(storage, escaping):
    escaping = escaping.format(outside=storage.parent / "outside.txt")
    with pytest.raises(StoredPathOutsideOwnerStorage):
        remove_stored_files([f"{OWNER}/source_markdown/a.md", escaping], owner_subject=OWNER)
    assert (storage / OWNER / "source_markdown/a.md").is_file()  # nothing was deleted
    assert (storage.parent / "outside.txt").is_file()
    assert (storage / "other-subject/source_markdown/victim.md").is_file()


def test_a_directory_is_never_removed_and_the_other_paths_are_still_tried(storage):
    with pytest.raises(StoredFilesNotRemoved):
        remove_stored_files(
            [f"{OWNER}/source_markdown", f"{OWNER}/processed_json/a.json"], owner_subject=OWNER,
        )
    assert (storage / OWNER / "source_markdown").is_dir()
    assert (storage / OWNER / "source_markdown/a.md").is_file()
    assert not (storage / OWNER / "processed_json/a.json").exists()  # tried after the failing one


def test_an_owner_subject_that_cannot_name_a_folder_refuses(storage):
    for subject in ("", ".", "..", "a/b"):
        with pytest.raises(StoredPathOutsideOwnerStorage):
            remove_stored_files([f"{OWNER}/source_markdown/a.md"], owner_subject=subject)


def test_a_link_is_deleted_as_a_link_and_its_target_is_kept(storage):
    # Review P2-1: a stored path that is a link to another file of the same curator.
    target = storage / OWNER / "source_markdown/b.md"
    link = storage / OWNER / "source_markdown/link.md"
    link.symlink_to(target)
    remove_stored_files([f"{OWNER}/source_markdown/link.md"], owner_subject=OWNER)
    assert not link.is_symlink() and not link.exists()
    assert target.is_file()


def test_a_link_to_a_directory_is_deleted_as_a_link(storage):
    link = storage / OWNER / "folder_link"
    link.symlink_to(storage / OWNER / "source_markdown", target_is_directory=True)
    remove_stored_files([f"{OWNER}/folder_link"], owner_subject=OWNER)
    assert not link.is_symlink()
    assert (storage / OWNER / "source_markdown/a.md").is_file()


def test_a_folder_link_that_leaves_the_owners_folder_refuses(storage):
    (storage / OWNER / "escape").symlink_to(storage / "other-subject", target_is_directory=True)
    with pytest.raises(StoredPathOutsideOwnerStorage):
        remove_stored_files([f"{OWNER}/escape/source_markdown/victim.md"], owner_subject=OWNER)
    assert (storage / "other-subject/source_markdown/victim.md").is_file()
