#!/usr/bin/env python3
"""
Match Google Drive inventory CSV entries to possible local originals.

Designed for CSV output from:
    https://github.com/turretadotcom/turreta-gdrive-hash-audit

Canonical input columns:
    path,name,id,mimeType,size,md5Checksum,sha1Checksum,sha256Checksum,
    createdTime,modifiedTime

Typical relationship:
    Google Drive: IMG_4802-rs.jpg
    Local:        IMG_4802.jpg

For videos, resizing/transcoding may also change the extension:
    Google Drive: family-video-rs.mp4
    Local:        family-video.mpg

Candidate discovery is based primarily on the filename stem after removing
"-rs". Extension equality is recorded but is not required unless
--strict-extension is supplied.

The script never modifies local or Google Drive files.
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

HASH_CHUNK_SIZE = 1024 * 1024

EXPECTED_GDRIVE_FIELDS = [
    "path",
    "name",
    "id",
    "mimeType",
    "size",
    "md5Checksum",
    "sha1Checksum",
    "sha256Checksum",
    "createdTime",
    "modifiedTime",
]


def split_normalized_name(filename: str, resize_suffix: str = "-rs") -> Tuple[str, str, bool]:
    filename = Path(filename).name
    stem, ext = os.path.splitext(filename)

    had_suffix = bool(
        resize_suffix
        and stem.casefold().endswith(resize_suffix.casefold())
    )

    if had_suffix:
        stem = stem[: -len(resize_suffix)]

    return stem.casefold(), ext.casefold(), had_suffix


def hash_file(path: Path) -> Tuple[str, str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()

    with path.open("rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)

    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def index_local_files(
    root: Path,
    resize_suffix: str,
    excluded_paths: Iterable[Path],
) -> Dict[str, List[Path]]:
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

            normalized_stem, _, _ = split_normalized_name(filename, resize_suffix)
            index[normalized_stem].append(path)

    return index


def classify_match(
    drive_name: str,
    local_name: str,
    resize_suffix: str,
) -> Tuple[str, bool]:
    gd_stem, gd_ext, gd_had_suffix = split_normalized_name(drive_name, resize_suffix)
    local_stem, local_ext, local_had_suffix = split_normalized_name(local_name, resize_suffix)

    same_ext = gd_ext == local_ext

    if drive_name.casefold() == local_name.casefold():
        return "exact_filename", same_ext

    if gd_stem != local_stem:
        return "unexpected", same_ext

    if gd_had_suffix and not local_had_suffix:
        if same_ext:
            return "resized_name_same_extension", True
        return "resized_name_extension_changed", False

    if not gd_had_suffix and local_had_suffix:
        if same_ext:
            return "local_has_rs_same_extension", True
        return "local_has_rs_extension_changed", False

    if same_ext:
        return "same_stem_same_extension", True

    return "same_stem_extension_changed", False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Match gdrive_hashes.csv entries from turreta-gdrive-hash-audit "
            "to possible local originals and calculate local checksums."
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
        help="Google Drive inventory CSV. Default: gdrive_hashes.csv",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("gdrive_local_matches.csv"),
        help="Output comparison CSV. Default: gdrive_local_matches.csv",
    )
    parser.add_argument(
        "--suffix",
        default="-rs",
        help='Resize suffix immediately before extension. Default: "-rs"',
    )
    parser.add_argument(
        "--only-rs",
        action="store_true",
        help="Process only Drive rows whose filename contains the resize suffix.",
    )
    parser.add_argument(
        "--include-unmatched",
        action="store_true",
        help="Write unmatched Drive records to the output too.",
    )
    parser.add_argument(
        "--strict-extension",
        action="store_true",
        help=(
            "Require the local file extension to equal the Drive extension. "
            "By default extension changes are allowed so transcoded videos "
            "such as movie.mpg -> movie-rs.mp4 can still be found."
        ),
    )

    args = parser.parse_args()

    input_csv = args.input.expanduser().resolve()
    output_csv = args.output.expanduser().resolve()
    local_root = args.local_root.expanduser().resolve()

    if not input_csv.is_file():
        print(f"ERROR: Input CSV not found: {input_csv}", file=sys.stderr)
        return 2

    if not local_root.is_dir():
        print(f"ERROR: Local root is not a directory: {local_root}", file=sys.stderr)
        return 2

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    print(f"Input CSV:      {input_csv}")
    print(f"Local root:     {local_root}")
    print(f"Output CSV:     {output_csv}")
    print(f"Resize suffix:  {args.suffix!r}")
    print(
        "Extension mode: "
        + ("strict" if args.strict_extension else "allow changed extensions")
    )
    print()

    print("Indexing local filenames...")
    local_index = index_local_files(
        local_root,
        args.suffix,
        excluded_paths=(input_csv, output_csv),
    )

    local_file_count = sum(len(v) for v in local_index.values())
    print(
        f"Indexed {local_file_count:,} local files under "
        f"{len(local_index):,} normalized stems."
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

        missing = [
            field for field in EXPECTED_GDRIVE_FIELDS
            if field not in reader.fieldnames
        ]

        if missing:
            print(
                "ERROR: Input does not match the expected "
                "turreta-gdrive-hash-audit CSV schema.",
                file=sys.stderr,
            )
            print("Missing columns: " + ", ".join(missing), file=sys.stderr)
            print("Found columns: " + ", ".join(reader.fieldnames), file=sys.stderr)
            return 2

        drive_fields = list(reader.fieldnames)

        extra_fields = [
            "normalized_stem",
            "drive_extension",
            "drive_has_resize_suffix",
            "match_status",
            "match_type",
            "same_extension",
            "candidate_count",
            "local_path",
            "local_name",
            "local_extension",
            "local_size",
            "local_mtime_ns",
            "local_md5",
            "local_sha1",
            "local_sha256",
            "hash_error",
        ]

        output_fields = drive_fields + extra_fields

        with output_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=output_fields)
            writer.writeheader()

            for drive_row in reader:
                rows_seen += 1

                drive_name = (drive_row.get("name") or "").strip()
                if not drive_name:
                    continue

                gd_stem, gd_ext, gd_had_suffix = split_normalized_name(
                    drive_name, args.suffix
                )

                if args.only_rs and not gd_had_suffix:
                    continue

                rows_processed += 1
                candidates = list(local_index.get(gd_stem, []))

                if args.strict_extension:
                    candidates = [
                        p for p in candidates
                        if split_normalized_name(p.name, args.suffix)[1] == gd_ext
                    ]

                candidates.sort(
                    key=lambda p: (
                        split_normalized_name(p.name, args.suffix)[1] != gd_ext,
                        str(p).casefold(),
                    )
                )

                if not candidates:
                    unmatched_drive_rows += 1

                    if args.include_unmatched:
                        out = dict(drive_row)
                        out.update(
                            {
                                "normalized_stem": gd_stem,
                                "drive_extension": gd_ext,
                                "drive_has_resize_suffix": gd_had_suffix,
                                "match_status": "unmatched",
                                "match_type": "",
                                "same_extension": "",
                                "candidate_count": 0,
                                "local_path": "",
                                "local_name": "",
                                "local_extension": "",
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

                    match_type, same_extension = classify_match(
                        drive_name,
                        candidate.name,
                        args.suffix,
                    )

                    _, local_ext, _ = split_normalized_name(
                        candidate.name, args.suffix
                    )

                    md5 = sha1 = sha256 = ""
                    local_size = ""
                    local_mtime_ns = ""
                    hash_error = ""

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
                            "normalized_stem": gd_stem,
                            "drive_extension": gd_ext,
                            "drive_has_resize_suffix": gd_had_suffix,
                            "match_status": "matched",
                            "match_type": match_type,
                            "same_extension": same_extension,
                            "candidate_count": len(candidates),
                            "local_path": str(candidate.resolve()),
                            "local_name": candidate.name,
                            "local_extension": local_ext,
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
    print("Completed.")
    print(f"Drive rows read:             {rows_seen:,}")
    print(f"Drive rows processed:        {rows_processed:,}")
    print(f"Drive rows with candidates:  {matched_drive_rows:,}")
    print(f"Drive rows without match:    {unmatched_drive_rows:,}")
    print(f"Candidate rows written:      {candidate_rows_written:,}")
    print(f"Unique local files hashed:   {len(hash_cache):,}")
    print(f"Hash/read errors:            {hash_errors:,}")
    print(f"Output: {output_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
