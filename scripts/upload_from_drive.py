#!/usr/bin/env python3
"""
Google Drive -> YouTube Shorts Auto-Uploader

Watches a Google Drive `Ready-to-Post` folder, downloads pending video files
(supports multi-GB files via streaming chunks), uploads them to YouTube via
YouTube Data API v3 resumable upload, optionally attaches a custom thumbnail,
and moves processed files into the Google Drive `Posted` folder.

Required environment variables:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
  GDRIVE_READY_FOLDER_ID
  GDRIVE_POSTED_FOLDER_ID
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
THUMB_EXTENSIONS = {".png", ".jpg", ".jpeg"}
TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB chunks for large videos


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"ERROR: Missing required environment variable: {name}")
    return value


def build_credentials(token_env: str, scopes: list[str]) -> Credentials:
    refresh_token = os.environ.get(token_env, "").strip() or require_env("GOOGLE_REFRESH_TOKEN")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=require_env("GOOGLE_CLIENT_ID"),
        client_secret=require_env("GOOGLE_CLIENT_SECRET"),
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds


def list_folder_files(drive: Any, folder_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        resp = (
            drive.files()
            .list(
                q=query,
                orderBy="createdTime asc",
                pageSize=100,
                fields="nextPageToken, files(id, name, mimeType, size, createdTime, parents)",
                pageToken=page_token,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_drive_file(drive: Any, file_id: str, dest: Path) -> None:
    request = drive.files().get_media(fileId=file_id)
    with dest.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=CHUNK_SIZE)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Downloading {dest.name}: {pct}%", flush=True)


def move_drive_file(drive: Any, file_id: str, from_folder: str, to_folder: str) -> None:
    drive.files().update(
        fileId=file_id,
        addParents=to_folder,
        removeParents=from_folder,
        fields="id, parents",
    ).execute()


def fallback_title_from_stem(stem: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    title = cleaned.title() if cleaned else "New Short"
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    return title[:100]


def load_metadata(
    stem: str,
    sidecar_path: Path | None,
    default_privacy: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if sidecar_path and sidecar_path.exists():
        meta = json.loads(sidecar_path.read_text(encoding="utf-8"))

    title = str(meta.get("title") or fallback_title_from_stem(stem)).strip()[:100]
    description = str(
        meta.get("description")
        or f"{title}\n\n#Shorts"
    ).strip()[:5000]
    tags = meta.get("tags") or ["shorts"]
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [str(t).strip() for t in tags if str(t).strip()][:30]

    privacy = str(meta.get("privacyStatus") or default_privacy).lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = default_privacy

    publish_at = meta.get("publishAt")
    status_block: dict[str, Any] = {
        "privacyStatus": "private" if publish_at else privacy,
        "selfDeclaredMadeForKids": bool(meta.get("madeForKids", False)),
    }
    if publish_at:
        status_block["publishAt"] = str(publish_at)

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(meta.get("categoryId") or "28"),
        },
        "status": status_block,
    }


def upload_video_resumable(
    youtube: Any,
    video_path: Path,
    body: dict[str, Any],
) -> dict[str, Any]:
    media = MediaFileUpload(
        str(video_path),
        chunksize=CHUNK_SIZE,
        resumable=True,
        mimetype="video/*",
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Uploading to YouTube: {pct}%", flush=True)
        except HttpError as err:
            if err.resp is not None and err.resp.status in RETRIABLE_STATUS_CODES:
                retries += 1
                if retries > 5:
                    raise
                sleep_s = min(60, 2**retries)
                print(
                    f"  Transient HTTP {err.resp.status}; retrying in {sleep_s}s...",
                    flush=True,
                )
                time.sleep(sleep_s)
            else:
                raise
    return response


def append_step_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload queued videos from Google Drive to YouTube."
    )
    parser.add_argument(
        "--max-uploads",
        type=int,
        default=int(os.environ.get("MAX_UPLOADS_PER_RUN", "1")),
        help="Maximum number of videos to upload in a single run (default: 1).",
    )
    parser.add_argument(
        "--default-privacy",
        choices=["private", "unlisted", "public"],
        default=os.environ.get("DEFAULT_PRIVACY", "private"),
        help="Fallback privacy status when not set in sidecar JSON (default: private).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"},
        help="Inspect queued videos and metadata without uploading or moving files.",
    )
    args = parser.parse_args()

    ready_folder_id = require_env("GDRIVE_READY_FOLDER_ID")
    posted_folder_id = require_env("GDRIVE_POSTED_FOLDER_ID")

    drive_creds = build_credentials("GOOGLE_REFRESH_TOKEN", DRIVE_SCOPES)
    yt_creds = build_credentials("YOUTUBE_REFRESH_TOKEN", YOUTUBE_SCOPES)
    drive = build("drive", "v3", credentials=drive_creds, cache_discovery=False)
    youtube = build("youtube", "v3", credentials=yt_creds, cache_discovery=False)

    items = list_folder_files(drive, ready_folder_id)
    by_name = {f["name"]: f for f in items}
    video_items = [
        f
        for f in items
        if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS
    ]

    if not video_items:
        print("No pending videos found in Ready-to-Post folder.")
        append_step_summary(["### YouTube Shorts Uploader", "- No pending videos in `Ready-to-Post`."])
        return 0

    print(f"Found {len(video_items)} pending video(s). Processing up to {args.max_uploads}...")
    summary_lines = ["### YouTube Shorts Uploader Results", ""]

    uploaded_count = 0
    for item in video_items[: args.max_uploads]:
        name = item["name"]
        stem = Path(name).stem
        size_mb = int(item.get("size", 0)) / (1024 * 1024)
        print(f"\n-> Processing: {name} ({size_mb:.1f} MB)")

        sidecar_item = by_name.get(f"{stem}.json")
        thumb_item = next(
            (by_name[f"{stem}{ext}"] for ext in THUMB_EXTENSIONS if f"{stem}{ext}" in by_name),
            None,
        )

        with tempfile.TemporaryDirectory(prefix="yt-upload-") as tmpdir:
            tmp = Path(tmpdir)
            sidecar_path = None
            if sidecar_item:
                sidecar_path = tmp / sidecar_item["name"]
                download_drive_file(drive, sidecar_item["id"], sidecar_path)

            body = load_metadata(stem, sidecar_path, args.default_privacy)
            print(f"  Title:   {body['snippet']['title']}")
            print(f"  Privacy: {body['status']['privacyStatus']}")
            if body["status"].get("publishAt"):
                print(f"  Publish: {body['status']['publishAt']}")

            if args.dry_run:
                print("  [DRY RUN] Skipping download, YouTube upload, and Drive move.")
                summary_lines.append(
                    f"- **[DRY RUN]** `{name}` -> `{body['snippet']['title']}` ({body['status']['privacyStatus']})"
                )
                continue

            video_path = tmp / name
            download_drive_file(drive, item["id"], video_path)

            try:
                result = upload_video_resumable(youtube, video_path, body)
            except HttpError as err:
                msg = err.content.decode("utf-8", errors="replace") if err.content else str(err)
                print(f"ERROR uploading {name}: {msg}", file=sys.stderr)
                if "quotaExceeded" in msg:
                    print("YouTube daily API quota reached. Stopping further uploads.", file=sys.stderr)
                    break
                return 1

            video_id = result["id"]
            video_url = f"https://youtube.com/shorts/{video_id}"
            print(f"  Uploaded! Video ID: {video_id} ({video_url})")

            if thumb_item:
                thumb_path = tmp / thumb_item["name"]
                download_drive_file(drive, thumb_item["id"], thumb_path)
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(thumb_path)),
                    ).execute()
                    print(f"  Custom thumbnail set from {thumb_item['name']}.")
                except HttpError as err:
                    print(f"  Warning: Could not set thumbnail (channel may need phone verification): {err}")

            # Move processed files to Posted folder in Google Drive
            move_drive_file(drive, item["id"], ready_folder_id, posted_folder_id)
            if sidecar_item:
                move_drive_file(drive, sidecar_item["id"], ready_folder_id, posted_folder_id)
            if thumb_item:
                move_drive_file(drive, thumb_item["id"], ready_folder_id, posted_folder_id)
            print("  Moved files to Drive Posted folder.")

            uploaded_count += 1
            summary_lines.append(
                f"- Uploaded `{name}` -> [{body['snippet']['title']}]({video_url}) (`{body['status']['privacyStatus']}`)"
            )

    append_step_summary(summary_lines)
    print(f"\nDone. Uploaded {uploaded_count} video(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
