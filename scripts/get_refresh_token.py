#!/usr/bin/env python3
"""
One-time local OAuth helper to generate a Google refresh token covering both:
  1. YouTube Data API v3 (youtube.upload)
  2. Google Drive API (drive — download from Ready-to-Post & move to Posted)

Run this ONCE on your own computer:
    pip install -r requirements.txt
    python scripts/get_refresh_token.py --client-secret client_secret.json
"""

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/drive",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a single OAuth refresh token for YouTube + Google Drive."
    )
    parser.add_argument(
        "--client-secret",
        default="client_secret.json",
        help="Path to the Desktop OAuth client_secret.json downloaded from Google Cloud Console.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local callback port for the OAuth browser redirect (default: 8080).",
    )
    args = parser.parse_args()

    secret_path = Path(args.client_secret)
    if not secret_path.exists():
        print(f"ERROR: Could not find {secret_path.resolve()}", file=sys.stderr)
        print(
            "Download a Desktop App OAuth 2.0 Client JSON from Google Cloud Console "
            "and save it as client_secret.json in this folder.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(
        port=args.port,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print(
            "ERROR: Google did not return a refresh token. "
            "Revoke prior access at https://myaccount.google.com/permissions and re-run.",
            file=sys.stderr,
        )
        return 1

    raw = json.loads(secret_path.read_text(encoding="utf-8"))
    client_cfg = raw.get("installed") or raw.get("web") or {}
    client_id = client_cfg.get("client_id", creds.client_id)
    client_secret = client_cfg.get("client_secret", creds.client_secret)

    print("\n" + "=" * 72)
    print("SUCCESS! Add these 3 secrets to your GitHub Repository Settings:")
    print("Settings -> Secrets and variables -> Actions -> New repository secret")
    print("=" * 72)
    print(f"GOOGLE_CLIENT_ID     = {client_id}")
    print(f"GOOGLE_CLIENT_SECRET = {client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
    print("=" * 72)
    print("Keep these private and NEVER commit client_secret.json to GitHub.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
