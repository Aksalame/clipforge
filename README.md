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
