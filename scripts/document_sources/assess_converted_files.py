#!/usr/bin/env python3
"""Run the per-file structural checks over converted text files on disk; print counts only.

The same function the app uses (``assess_converted_file``) runs over every
file under the given directories. The output holds counts by result and
reason, never file names, ids or text. Fetching files from a provider is not
done here.

    python scripts/document_sources/assess_converted_files.py DIR [DIR ...] \\
        [--single-title] [--pattern '*.md'] [--json]

Thresholds come from the same settings as the app
(``DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES``, ``DOCUMENT_FILE_FEW_CHARACTERS_NOTE_BELOW``,
``EMBEDDING_MODEL_TOKEN_LIMIT`` minus ``EMBEDDING_TOKEN_SAFETY_MARGIN``) and the
token count uses the ``EMBEDDING_MODEL`` encoding.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--pattern", default="*.md", help="File name pattern (default: *.md).")
    parser.add_argument(
        "--single-title",
        action="store_true",
        help="Apply R7 (exactly one top-level title), as for converted Word and Excel files.",
    )
    parser.add_argument("--json", action="store_true", help="Print the counts as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from src.lib.document_sources.file_assessment import FileProfile, summarize_assessments
    from src.lib.document_sources.file_listing import current_file_check_limits

    paths = sorted(
        path for directory in args.directories for path in directory.rglob(args.pattern) if path.is_file()
    )
    summary = summarize_assessments(
        (path.read_bytes() for path in paths),
        FileProfile(single_title_required=args.single_title),
        current_file_check_limits(),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
