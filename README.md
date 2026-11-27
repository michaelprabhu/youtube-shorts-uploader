# YouTube Shorts Auto-Uploader (Google Drive → YouTube)

Automatically pulls queued `.mp4` Shorts (of **any file size** — no GitHub 25 MB limit!) from your Google Drive `Ready-to-Post` folder, uploads them to YouTube via **YouTube Data API v3**, and moves completed files to `Posted`.

## How It Works

1. Drop `<name>.mp4` (and optional `<name>.json` metadata + optional `<name>.png`/`.jpg` thumbnail) into your Google Drive **`Antigravity/Ready-to-Post`** folder.
2. GitHub Actions runs on schedule (default: daily at `16:00 UTC`) — or anytime you click **Actions → Upload Shorts from Google Drive → Run workflow**.
3. The script downloads the video in 10 MB chunks, uploads it to YouTube with resumable retries, and moves the files into **`Antigravity/Posted`** so nothing double-posts.

---

## One-Time Setup (~5 Minutes)

### Step 1: Enable APIs in Google Cloud Console
1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project (e.g., `youtube-shorts-uploader`).
2. Go to **APIs & Services → Library** and enable both:
   - **YouTube Data API v3**
   - **Google Drive API**
3. Go to **Google Auth Platform → Audience / OAuth consent screen**:
   - Choose **External**, fill in App Name + your email.
   - Under **Test users**, add your own Gmail address (the one that owns your YouTube channel & Drive).
4. Go to **Clients / Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Download the JSON file and save it locally as `client_secret.json` (do **not** commit it).

### Step 2: Generate Your Refresh Token Once (in Your Own Browser)
On your computer, inside this repository folder:

```bash
pip install -r requirements.txt
python scripts/get_refresh_token.py --client-secret client_secret.json
```

A browser window opens → sign into your Google/YouTube account and click **Allow**.
The script prints your 3 secrets (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`).

### Step 3: Add Secrets to Your Private GitHub Repository
Go to **GitHub Repo → Settings → Secrets and variables → Actions → New repository secret** and add these 5 secrets:

| Secret Name | Value |
| :--- | :--- |
| `GOOGLE_CLIENT_ID` | Printed by `get_refresh_token.py` |
| `GOOGLE_CLIENT_SECRET` | Printed by `get_refresh_token.py` |
| `GOOGLE_REFRESH_TOKEN` | Printed by `get_refresh_token.py` |
| `GDRIVE_READY_FOLDER_ID` | `1Twmal9gscq65NyTlsW4Q3vff4PtMI9Si` *(your `Antigravity/Ready-to-Post` folder)* |
| `GDRIVE_POSTED_FOLDER_ID` | `1LE7_QaKJ7du3ndWbKxii7p2iZfDCr9eM` *(your `Antigravity/Posted` folder)* |

### Step 4: Test with a Dry Run
1. Go to **Actions → Upload Shorts from Google Drive → Run workflow**.
2. Toggle **dry_run = true** and click **Run workflow**.
3. Once you see your queued video listed cleanly in the summary, re-run with **dry_run = false**!

---

## Sidecar Metadata Format (Optional)

For `my-short.mp4`, drop a matching `my-short.json` next to it in `Ready-to-Post` (see `examples/sample-video.json`):

```json
{
  "title": "Your Viral Hook Title #Shorts",
  "description": "Short description + engagement question.\n\n#Shorts #Topic",
  "tags": ["shorts", "topic"],
  "categoryId": "28",
  "privacyStatus": "private",
  "madeForKids": false,
  "publishAt": null
}
```

- If no `.json` file is provided, the script automatically builds a title from the filename + `#Shorts`.
- **Note on Privacy:** Unverified Google Cloud OAuth apps default to **Private** uploads on YouTube until Google verifies the app (you can flip Private → Public in 1 tap in YouTube Studio, or submit for free YouTube API verification for full auto-Public).
- **Daily Free Quota:** 10,000 units/day (~1,600 units per upload = up to 6 Shorts/day).
