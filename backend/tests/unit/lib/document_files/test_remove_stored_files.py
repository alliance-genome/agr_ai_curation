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


def test_zero_removable_paths_still_rejects_symlinked_owner_directory(tmp_path):
    """Owner-directory symlink validation must run even when a paper has 0 removable file paths."""
    from fastapi import HTTPException
    from src.api import documents as documents_api
    from src.lib.document_files.service import (
        remove_paper_stored_files,
        require_removable_paper_paths,
    )

    base_storage = tmp_path / "storage"
    base_storage.mkdir()
    outside = tmp_path / "outside"
    doc_id = "11111111-2222-3333-4444-555555555555"
    outside_supp = outside / doc_id / "supplement_pdf"
    outside_supp.mkdir(parents=True)

    (base_storage / OWNER).symlink_to(outside, target_is_directory=True)
    empty_cleanup = {"files": []}

    with pytest.raises(StoredPathOutsideOwnerStorage):
        require_removable_paper_paths(empty_cleanup, base_storage, OWNER)

    with pytest.raises(StoredPathOutsideOwnerStorage):
        remove_paper_stored_files(
            empty_cleanup,
            base_storage=base_storage,
            user_sub=OWNER,
            document_id=doc_id,
        )

    with pytest.raises(HTTPException) as exc_info:
        documents_api._require_removable_paths(empty_cleanup, base_storage, OWNER)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == (
        "This paper could not be deleted: one of its stored files needs an administrator. Nothing was changed."
    )

    with pytest.raises(StoredPathOutsideOwnerStorage):
        documents_api._remove_empty_paper_folders(base_storage, OWNER, doc_id)

    assert outside_supp.is_dir()


def test_empty_folder_pruning_never_follows_symlinked_paper_dir_into_supplement_pdf(tmp_path):
    """A symlinked paper_dir must never prune an empty supplement_pdf subfolder in its target."""
    from src.api import documents as documents_api
    from src.lib.document_files.service import remove_paper_stored_files

    base_storage = tmp_path / "storage"
    owner_dir = base_storage / OWNER
    owner_dir.mkdir(parents=True)

    target_dir = tmp_path / "external_target"
    target_supp = target_dir / "supplement_pdf"
    target_supp.mkdir(parents=True)

    doc_id = "22222222-3333-4444-5555-666666666666"
    paper_link = owner_dir / doc_id
    paper_link.symlink_to(target_dir, target_is_directory=True)

    remove_paper_stored_files(
        {"files": []},
        base_storage=base_storage,
        user_sub=OWNER,
        document_id=doc_id,
    )
    documents_api._remove_empty_paper_folders(base_storage, OWNER, doc_id)

    assert paper_link.is_symlink()
    assert target_supp.is_dir()
    assert target_dir.is_dir()
