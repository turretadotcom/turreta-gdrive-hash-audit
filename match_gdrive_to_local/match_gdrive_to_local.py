#!/usr/bin/env python3
"""
Match Google Drive CSV entries to possible local originals by filename.

Example:
    Google Drive CSV name: IMG_4802-rs.jpg
    Local file:            IMG_4802.jpg

The script:
1. Reads gdrive_hashes.csv.
2. Recursively indexes filenames under a local root directory.
3. Removes a configurable suffix (default: "-rs") immediately before the
   extension when building comparison keys.
4. Finds all possible local filename matches.
5. Computes MD5, SHA-1, and SHA-256 for each matched local file.
6. Writes the results to a new CSV.

No file contents are modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def normalize_filename(filename: str, resize_suffix: str = "-rs") -> str:
    """
    Return a case-insensitive comparison key.

    Examples:
        IMG_4802-rs.jpg -> img_4802.jpg
        IMG_4802.jpg    -> img_4802.jpg
        movie-RS.MP4    -> movie.mp4

    Only a suffix immediately before the final extension is removed.
    """
    filename = Path(filename).name
    stem, ext = os.path.splitext(filename)

    if resize_suffix and stem.lower().endswith(resize_suffix.lower()):
        stem = stem[: -len(resize_suffix)]

    return (stem + ext).casefold()


def hash_file(path: Path) -> Tuple[str, str, str]:
    """Return (md5, sha1, sha256) for a file in one read pass."""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)

    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def index_local_files(
    root: Path,
    resize_suffix: str,
    excluded_paths: Iterable[Path],
) -> Dict[str, List[Path]]:
    """
    Recursively index local files by normalized filename.

    Multiple files may have the same normalized name, so each key maps to
    a list of possible matches.
    """
    excluded = set()

    for p in excluded_paths:
        try:
            excluded.add(p.resolve())
        except OSError:
            pass

    index: Dict[str, List[Path]] = defaultdict(list)

    for dirpath, _, filenames in os.walk(root):
        directory = Path(dirpath)

        for filename in filenames:
            path = directory / filename

            try:
                resolved = path.resolve()
            except OSError:
                resolved = path

            if resolved in excluded:
                continue

            key = normalize_filename(filename, resize_suffix)
            index[key].append(path)

    return index


def detect_match_type(
    gdrive_name: str,
    local_name: str,
    resize_suffix: str,
) -> str:
    """Describe why two filenames matched."""
    if gdrive_name.casefold() == local_name.casefold():
        return "exact_filename"

    gd_stem, gd_ext = os.path.splitext(gdrive_name)
    local_stem, local_ext = os.path.splitext(local_name)

    gd_has_suffix = (
        bool(resize_suffix)
        and gd_stem.lower().endswith(resize_suffix.lower())
    )
    local_has_suffix = (
        bool(resize_suffix)
        and local_stem.lower().endswith(resize_suffix.lower())
    )

    if gd_has_suffix and not local_has_suffix:
        return "gdrive_rs_to_local_original"

    if not gd_has_suffix and local_has_suffix:
        return "gdrive_original_to_local_rs"

    return "normalized_filename"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Match files listed in a Google Drive hash CSV against local "
            "files using filenames, treating '-rs' before the extension "
            "as a resize suffix."
        )
    )

    parser.add_argument(
        "local_root",
        type=Path,
        help="Local directory to search recursively.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("gdrive_hashes.csv"),
        help="Input Google Drive CSV. Default: gdrive_hashes.csv",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("gdrive_local_matches.csv"),
        help="Output CSV. Default: gdrive_local_matches.csv",
    )
    parser.add_argument(
        "--suffix",
        default="-rs",
        help='Resize suffix before the extension. Default: "-rs"',
    )
    parser.add_argument(
        "--only-rs",
        action="store_true",
        help="Only process Google Drive rows whose filename has the resize suffix.",
    )
    parser.add_argument(
        "--include-unmatched",
        action="store_true",
        help="Also write Google Drive rows for which no local candidate was found.",
    )

    args = parser.parse_args()

    input_csv = args.input.expanduser()
    output_csv = args.output.expanduser()
    local_root = args.local_root.expanduser()

    if not input_csv.is_file():
        print(f"ERROR: Input CSV not found: {input_csv}", file=sys.stderr)
        return 2

    if not local_root.is_dir():
        print(f"ERROR: Local root is not a directory: {local_root}", file=sys.stderr)
        return 2

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reading:       {input_csv}")
    print(f"Local root:    {local_root}")
    print(f"Output:        {output_csv}")
    print(f"Resize suffix: {args.suffix!r}")
    print()
    print("Indexing local filenames...")

    local_index = index_local_files(
        local_root,
        args.suffix,
        excluded_paths=(input_csv, output_csv),
    )

    local_file_count = sum(len(paths) for paths in local_index.values())
    print(
        f"Indexed {local_file_count:,} files "
        f"under {len(local_index):,} normalized filenames."
    )

    hash_cache: Dict[Path, Tuple[str, str, str]] = {}

    rows_seen = 0
    rows_processed = 0
    matched_drive_rows = 0
    unmatched_drive_rows = 0
    candidate_rows_written = 0
    hash_errors = 0

    with input_csv.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)

        if not reader.fieldnames:
            print("ERROR: CSV has no header.", file=sys.stderr)
            return 2

        if "name" not in reader.fieldnames:
            print(
                "ERROR: CSV must contain a 'name' column. "
                f"Found: {reader.fieldnames}",
                file=sys.stderr,
            )
            return 2

        drive_fields = list(reader.fieldnames)

        extra_fields = [
            "normalized_name",
            "match_status",
            "match_type",
            "candidate_count",
            "local_path",
            "local_name",
            "local_size",
            "local_mtime_ns",
            "local_md5",
            "local_sha1",
            "local_sha256",
            "hash_error",
        ]

        output_fields = drive_fields + extra_fields

        with output_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.DictWriter(
                dst,
                fieldnames=output_fields,
                extrasaction="ignore",
            )
            writer.writeheader()

            for drive_row in reader:
                rows_seen += 1
                drive_name = (drive_row.get("name") or "").strip()

                if not drive_name:
                    continue

                gd_stem, _ = os.path.splitext(drive_name)
                drive_has_rs = (
                    bool(args.suffix)
                    and gd_stem.lower().endswith(args.suffix.lower())
                )

                if args.only_rs and not drive_has_rs:
                    continue

                rows_processed += 1
                normalized_name = normalize_filename(drive_name, args.suffix)
                candidates = local_index.get(normalized_name, [])

                if not candidates:
                    unmatched_drive_rows += 1

                    if args.include_unmatched:
                        out = dict(drive_row)
                        out.update(
                            {
                                "normalized_name": normalized_name,
                                "match_status": "unmatched",
                                "match_type": "",
                                "candidate_count": 0,
                                "local_path": "",
                                "local_name": "",
                                "local_size": "",
                                "local_mtime_ns": "",
                                "local_md5": "",
                                "local_sha1": "",
                                "local_sha256": "",
                                "hash_error": "",
                            }
                        )
                        writer.writerow(out)

                    continue

                matched_drive_rows += 1

                for candidate in candidates:
                    candidate_rows_written += 1
                    hash_error = ""
                    md5 = sha1 = sha256 = ""
                    local_size = ""
                    local_mtime_ns = ""

                    try:
                        stat = candidate.stat()
                        local_size = stat.st_size
                        local_mtime_ns = stat.st_mtime_ns

                        if candidate not in hash_cache:
                            hash_cache[candidate] = hash_file(candidate)

                        md5, sha1, sha256 = hash_cache[candidate]

                    except (OSError, PermissionError) as exc:
                        hash_errors += 1
                        hash_error = f"{type(exc).__name__}: {exc}"

                    out = dict(drive_row)
                    out.update(
                        {
                            "normalized_name": normalized_name,
                            "match_status": "matched",
                            "match_type": detect_match_type(
                                drive_name,
                                candidate.name,
                                args.suffix,
                            ),
                            "candidate_count": len(candidates),
                            "local_path": str(candidate.resolve()),
                            "local_name": candidate.name,
                            "local_size": local_size,
                            "local_mtime_ns": local_mtime_ns,
                            "local_md5": md5,
                            "local_sha1": sha1,
                            "local_sha256": sha256,
                            "hash_error": hash_error,
                        }
                    )
                    writer.writerow(out)

    print()
    print("Done.")
    print(f"Google Drive rows read:       {rows_seen:,}")
    print(f"Google Drive rows processed:  {rows_processed:,}")
    print(f"Drive rows with local match:  {matched_drive_rows:,}")
    print(f"Drive rows without match:     {unmatched_drive_rows:,}")
    print(f"Candidate rows written:       {candidate_rows_written:,}")
    print(f"Unique local files hashed:    {len(hash_cache):,}")
    print(f"Hash/read errors:             {hash_errors:,}")
    print(f"Output CSV: {output_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
