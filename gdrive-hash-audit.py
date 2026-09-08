from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Dict, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"


def get_credentials(credentials_file: Path, token_file: Path) -> Credentials:
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


def list_drive_files(service) -> List[dict]:
    files: List[dict] = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q="trashed = false",
                pageSize=1000,
                pageToken=page_token,
                spaces="drive",
                fields=(
                    "nextPageToken,files("
                    "id,name,mimeType,size,md5Checksum,sha1Checksum,sha256Checksum,"
                    "parents,createdTime,modifiedTime)"
                ),
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def build_paths(files: List[dict]) -> Dict[str, str]:
    by_id = {f["id"]: f for f in files}
    cache: Dict[str, str] = {}

    def resolve(file_id: str, stack: set[str] | None = None) -> str:
        if file_id in cache:
            return cache[file_id]

        stack = stack or set()
        if file_id in stack:
            return by_id[file_id].get("name", file_id)
        stack.add(file_id)

        item = by_id[file_id]
        name = item.get("name", file_id)
        parents = item.get("parents", [])

        if not parents or parents[0] not in by_id:
            path = name
        else:
            parent_path = resolve(parents[0], stack)
            path = f"{parent_path}/{name}"

        cache[file_id] = path
        return path

    for file_id in by_id:
        resolve(file_id)

    return cache


def export_csv(records: List[dict], output: Path) -> None:
    fields = [
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
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({key: row.get(key, "") for key in fields})


def export_json(records: List[dict], output: Path) -> None:
    output.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    credentials_file = Path(args.credentials)
    token_file = Path(args.token)

    if not credentials_file.exists() and not token_file.exists():
        raise SystemExit(
            f"Credentials file not found: {credentials_file}\n"
            "Create an OAuth Desktop App in Google Cloud and download its JSON credentials."
        )

    creds = get_credentials(credentials_file, token_file)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    files = list_drive_files(service)
    paths = build_paths(files)

    records = []
    for item in files:
        if item.get("mimeType") == FOLDER_MIME and not args.include_folders:
            continue
        row = dict(item)
        row["path"] = paths[item["id"]]
        records.append(row)

    records.sort(key=lambda r: r["path"].lower())

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "csv":
        export_csv(records, output)
    else:
        export_json(records, output)

    hashed = sum(
        1
        for r in records
        if r.get("md5Checksum") or r.get("sha1Checksum") or r.get("sha256Checksum")
    )
    print(f"Drive items exported: {len(records)}")
    print(f"Items with at least one checksum: {hashed}")
    print(f"Output: {output.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Google Drive file metadata and available checksums."
    )
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument("--token", default="token.json")
    parser.add_argument("--output", default="gdrive_hashes.csv")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--include-folders", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
