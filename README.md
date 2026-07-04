# ClipForge

An OpusClip-style app: upload a long video, get back short, captioned,
vertically-reframed highlight clips.

## What's real vs. what needs your keys

Everything runs for real — nothing here is mocked:

| Step | How it works | Needs internet? |
|---|---|---|
| Cut clips | ffmpeg, frame-accurate re-encode | No — local |
| Vertical 9:16 reframe | OpenCV face detection samples 8 frames per clip and centers the crop on the largest detected face (falls back to a center crop if no face found) | No — local |
| Burned-in captions | Word-level timestamps chunked into punchy 3-4 word captions, rendered via ffmpeg's subtitles filter | No — local |
| Transcription | OpenAI Whisper API (`whisper-1`, word timestamps) | **Yes** — needs your `OPENAI_API_KEY` |
| Highlight selection | Claude (`claude-sonnet-5`) reads the timestamped transcript and picks the strongest self-contained moments | **Yes** — needs your `ANTHROPIC_API_KEY` |

You paste both API keys into the page itself — they're sent straight from
your browser to your local server and used per-request, never written to
disk.

## Setup

```bash
pip install -r requirements.txt
# ffmpeg must be installed and on your PATH (brew install ffmpeg / apt install ffmpeg)
python app.py
```

Then open **http://localhost:5050**.

## YouTube + Instagram automation (optional)

This adds: fetching a video directly from a YouTube link, uploading generated
clips to YouTube Shorts, publishing them as Instagram Reels, and scheduling
posts for later. All of it is real, working code — but both platforms
require you to register your own "app" with Google / Meta first. There's no
way around this setup; it's how every automation tool (including the real
OpusClip) works under the hood.

### 1. YouTube Shorts upload — Google Cloud setup

1. Go to **console.cloud.google.com**, create a new project.
2. **APIs & Services → Library** → search "YouTube Data API v3" → Enable.
3. **APIs & Services → OAuth consent screen** → set up as "External", add
   your email, add the scope `https://www.googleapis.com/auth/youtube.upload`.
   While in testing mode, add your own Google account as a "test user".
4. **APIs & Services → Credentials → Create Credentials → OAuth Client ID**
   → Application type: "Web application".
   → Under "Authorized redirect URIs" add:
   `https://YOUR-RENDER-URL.onrender.com/auth/youtube/callback`
5. Copy the **Client ID** and **Client Secret**.
6. In Render: your service → **Environment** tab → add:
   - `GOOGLE_CLIENT_ID` = your client id
   - `GOOGLE_CLIENT_SECRET` = your client secret
   - `APP_BASE_URL` = `https://YOUR-RENDER-URL.onrender.com`
7. Redeploy. Open your site → "Connect YouTube" button → sign in → done.

### 2. Instagram Reels — Meta Developer setup

Instagram automation **requires** a Business or Creator Instagram account
linked to a Facebook Page (personal accounts can't be automated).

1. Convert your Instagram account to Business/Creator (Instagram app →
   Settings → Account type) and link it to a Facebook Page.
2. Go to **developers.facebook.com** → "My Apps" → "Create App" → type
   "Business".
3. Add the **Instagram Graph API** product to your app.
4. In **App Settings → Basic**, copy the **App ID** and **App Secret**.
5. In **Facebook Login → Settings**, add this as a valid OAuth redirect URI:
   `https://YOUR-RENDER-URL.onrender.com/auth/instagram/callback`
6. While your app is in "Development" mode, add yourself as a Tester/Admin
   under **App Roles** so you can log in and test.
7. In Render, add environment variables:
   - `FB_APP_ID` = your app id
   - `FB_APP_SECRET` = your app secret
8. Redeploy. Open your site → "Connect Instagram" button → log in with
   Facebook → done.

Note: to go live for real (not just your own test account), Meta requires
App Review for the `instagram_content_publish` permission — this is Meta's
process, not something in this codebase.

### 3. Scheduling — keeping it reliable on free hosting

Scheduled posts are checked once a minute by an in-process scheduler, but
Render's **free tier spins the app down after 15 minutes idle**, so a
scheduled post could get missed if nothing woke the app up in time.

Fix: use a free external cron service to ping a "check now" endpoint every
few minutes, which both wakes the app up and triggers publishing:

1. Go to **cron-job.org** (free), sign up.
2. Create a new cron job:
   - URL: `https://YOUR-RENDER-URL.onrender.com/cron/publish-due`
   - Schedule: every 5 minutes
3. Save. Now scheduled posts will go out even if the app was asleep.

## How it works, end to end

1. **Upload** — video is saved locally, ffprobe reads duration/resolution.
2. **Transcribe** — audio is extracted with ffmpeg and sent to Whisper for a
   word-level timestamped transcript.
3. **Find highlights** — the full transcript (with timestamps) is sent to
   Claude, which returns a JSON list of the strongest 20-90s moments with
   titles and reasons.
4. **Generate clip** (per highlight, on demand):
   - ffmpeg cuts the exact time range
   - OpenCV runs face detection across sampled frames in that range to pick
     the best horizontal crop for a 9:16 frame
   - a `.srt` is built from the word timestamps that fall in that window and
     burned in via ffmpeg's `subtitles` filter
   - final 1080x1920 MP4 is written to `clips/` and shown in the gallery

## Notes / things you'd want to change for production

- Jobs are stored in an in-memory dict (`JOBS`) — fine for one person running
  this locally, but swap for Redis/a database if multiple people will use it
  at once, since it doesn't persist across restarts or scale across workers.
- No auth, no rate limiting, no HTTPS — add these before exposing it beyond
  your own machine.
- The smart-crop uses a single averaged offset per clip (Haar cascade face
  detection). For per-frame dynamic tracking (camera "follows" a moving
  speaker) you'd want a per-frame crop path with smoothing — more ffmpeg
  filter complexity but same underlying approach.
- Large uploads: `MAX_CONTENT_LENGTH` is set to 2GB; adjust for your needs.
- Tokens and scheduled posts live in a local SQLite file (`data.db`). Render's
  free disk isn't guaranteed to survive every redeploy — for anything you
  care about long-term, move this to a managed Postgres (Render has a free
  one) and swap out `db.py`'s sqlite3 calls for it.
