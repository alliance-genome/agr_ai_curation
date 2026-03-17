#!/usr/bin/env python3

import json
import os
import pathlib
import shutil
import sys
import tarfile


def fail(message: str) -> None:
    raise SystemExit(message)


def ensure_safe_relative_path(rel_path: str) -> pathlib.Path:
    candidate = pathlib.PurePosixPath(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        fail(f"Refusing unsafe relative path: {rel_path}")
    return pathlib.Path(candidate.as_posix())


def ensure_within_source_root(source_root: pathlib.Path, target_path: pathlib.Path, rel_path: str) -> pathlib.Path:
    resolved_target = target_path.resolve()
    if os.path.commonpath([str(source_root), str(resolved_target)]) != str(source_root):
        fail(f"Refusing path outside source root: {rel_path}")
    return resolved_target


def apply_archive(archive_path: pathlib.Path, source_root: pathlib.Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()

        for member in members:
            safe_rel_path = ensure_safe_relative_path(member.name)
            if member.issym() or member.islnk():
                fail(f"Refusing link entry from guest archive: {member.name}")
            if not member.isfile():
                fail(f"Refusing unsupported tar entry: {member.name}")
            ensure_within_source_root(source_root, source_root / safe_rel_path, member.name)

        for member in members:
            safe_rel_path = pathlib.Path(member.name)
            target_path = ensure_within_source_root(source_root, source_root / safe_rel_path, member.name)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                fail(f"Failed to read tar member: {member.name}")
            with extracted, target_path.open("wb") as output:
                output.write(extracted.read())
            os.chmod(target_path, member.mode & 0o777)


def apply_deletions(deleted_json: pathlib.Path, source_root: pathlib.Path) -> None:
    with deleted_json.open("r", encoding="utf-8") as handle:
        deleted_paths = json.load(handle)

    for rel_path in deleted_paths:
        safe_rel_path = ensure_safe_relative_path(rel_path)
        target_path = ensure_within_source_root(source_root, source_root / safe_rel_path, rel_path)
        if target_path.is_symlink() or target_path.is_file():
            target_path.unlink(missing_ok=True)
        elif target_path.is_dir():
            shutil.rmtree(target_path)


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: symphony_microvm_apply_guest_changes.py <changes-archive> <deleted-json> <source-root>",
            file=sys.stderr,
        )
        return 2

    archive_path = pathlib.Path(sys.argv[1]).resolve()
    deleted_json = pathlib.Path(sys.argv[2]).resolve()
    source_root = pathlib.Path(sys.argv[3]).resolve()

    if not source_root.is_dir():
        fail(f"Source root does not exist: {source_root}")

    if archive_path.exists():
        apply_archive(archive_path, source_root)

    if deleted_json.exists():
        apply_deletions(deleted_json, source_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
