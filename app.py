"""
ClipForge — an OpusClip-style app.
Real pipeline: ffmpeg for cutting/cropping/caption-burning, OpenCV for
face-detection-based smart vertical reframing, OpenAI Whisper API for
transcription, and Claude (Anthropic API) for highlight selection.

Only the transcription and highlight-selection steps call out to the
internet (OpenAI + Anthropic APIs) — you supply your own API keys.
Everything else (cutting, cropping, caption burning) runs 100% locally
with ffmpeg/OpenCV.
"""

import os
import re
import json
import uuid
import shutil
import subprocess
import urllib.request
import urllib.error
import mimetypes

import cv2
import numpy as np
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect

import db
import youtube
import instagram
import scheduler as sched_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CLIPS_DIR = os.path.join(BASE_DIR, "clips")
TMP_DIR = os.path.join(BASE_DIR, "tmp")
for d in (UPLOAD_DIR, CLIPS_DIR, TMP_DIR):
    os.makedirs(d, exist_ok=True)

db.init_db()
_background_scheduler = sched_module.start_background_scheduler()

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5050")

# In-memory job store (fine for a single-user local app; swap for a DB/Redis
# if you deploy this for multiple concurrent users).
JOBS = {}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB uploads

CLAUDE_MODEL = "claude-sonnet-5"
WHISPER_MODEL = "whisper-1"


# --------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# --------------------------------------------------------------------------

def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{result.stderr.decode(errors='ignore')}"
        )
    return result.stdout.decode(errors="ignore")


def ffprobe_duration(path):
    out = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    return float(json.loads(out)["format"]["duration"])


def ffprobe_dimensions(path):
    out = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path,
    ])
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


def extract_audio(video_path, audio_path):
    run([
        "ffmpeg", "-y", "-i", video_path, "-vn",
        "-ar", "16000", "-ac", "1", "-b:a", "64k", audio_path,
    ])


# --------------------------------------------------------------------------
# OpenAI Whisper transcription (network call — needs OPENAI key from user)
# --------------------------------------------------------------------------

def call_openai_whisper(audio_path, api_key):
    boundary = uuid.uuid4().hex
    fields = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    filename = os.path.basename(audio_path)
    mime = mimetypes.guess_type(filename)[0] or "audio/mpeg"

    body = bytearray()
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    with open(audio_path, "rb") as f:
        body += f.read()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI Whisper error {e.code}: {e.read().decode(errors='ignore')}")


# --------------------------------------------------------------------------
# Claude highlight selection (network call — needs ANTHROPIC key from user)
# --------------------------------------------------------------------------

def call_claude_highlights(transcript_text, duration, api_key, num_clips=5):
    prompt = f"""You are selecting the best short-form highlight clips from a video transcript,
the way a viral clips editor would. The full video is {duration:.0f} seconds long.

Transcript with timestamps (seconds):
{transcript_text}

Pick the {num_clips} strongest, most self-contained highlight moments. Each should:
- Be 20-90 seconds long
- Start and end at natural sentence/thought boundaries
- Work as a standalone clip without needing outside context
- Have a hook, story beat, punchline, or strong claim

Respond with ONLY a JSON array, no other text, no markdown fences:
[{{"start": <seconds:number>, "end": <seconds:number>, "title": "<short punchy title>", "reason": "<why this clip works, one sentence>"}}]
"""
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error {e.code}: {e.read().decode(errors='ignore')}")

    text = "".join(block.get("text", "") for block in data.get("content", []))
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


# --------------------------------------------------------------------------
# Smart vertical reframe using OpenCV face detection (100% local, no network)
# --------------------------------------------------------------------------

try:
    FACE_CASCADE = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if FACE_CASCADE.empty():
        FACE_CASCADE = None
except Exception:
    # Some hosting environments ship a cv2 build without CascadeClassifier
    # support. Degrade gracefully to a plain center crop instead of crashing.
    FACE_CASCADE = None


def compute_smart_crop_x(video_path, start, end, src_w, src_h, target_w):
    """Sample frames across the clip, detect faces, and return the best
    x-offset (in source pixels) for a target_w-wide vertical crop that keeps
    faces centered. Falls back to a centered crop if no faces are found."""
    if FACE_CASCADE is None:
        avg_center = src_w / 2
        x = avg_center - target_w / 2
        return int(max(0, min(x, src_w - target_w)))

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    n_samples = 8
    centers = []

    for i in range(n_samples):
        t = start + (end - start) * (i / max(1, n_samples - 1))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) == 0:
            continue
        # Weight by face area; use the largest/most prominent face's center.
        biggest = max(faces, key=lambda f: f[2] * f[3])
        fx, fy, fw, fh = biggest
        centers.append(fx + fw / 2)

    cap.release()

    if centers:
        avg_center = float(np.mean(centers))
    else:
        avg_center = src_w / 2  # no faces found -> center crop

    x = avg_center - target_w / 2
    x = max(0, min(x, src_w - target_w))
    return int(x)


# --------------------------------------------------------------------------
# Caption (.srt) generation from Whisper word-level timestamps
# --------------------------------------------------------------------------

def build_srt_for_window(words, clip_start, clip_end, words_per_caption=4):
    """words: list of {word, start, end} from Whisper. Builds punchy, chunked
    captions (OpusClip-style) shifted to be relative to the clip start."""
    in_window = [w for w in words if w["start"] >= clip_start and w["end"] <= clip_end]
    lines = []
    idx = 1
    for i in range(0, len(in_window), words_per_caption):
        chunk = in_window[i:i + words_per_caption]
        if not chunk:
            continue
        start = chunk[0]["start"] - clip_start
        end = chunk[-1]["end"] - clip_start
        text = " ".join(w["word"].strip() for w in chunk)
        lines.append((idx, max(0, start), max(0, end), text))
        idx += 1
    return lines


def srt_timestamp(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(lines, path):
    with open(path, "w", encoding="utf-8") as f:
        for idx, start, end, text in lines:
            f.write(f"{idx}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n\n")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/fetch-youtube", methods=["POST"])
def fetch_youtube():
    data = request.get_json(force=True)
    url = data.get("url", "").strip()
    if not url:
        return jsonify(error="No YouTube URL provided"), 400

    job_id = uuid.uuid4().hex[:12]
    out_template = os.path.join(UPLOAD_DIR, f"{job_id}.%(ext)s")

    try:
        run([
            "yt-dlp", "-f", "mp4/bestvideo+bestaudio",
            "--merge-output-format", "mp4",
            "-o", out_template, url,
        ])
    except Exception as e:
        return jsonify(error=f"Could not download video: {e}"), 400

    video_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if not os.path.exists(video_path):
        return jsonify(error="Download finished but the mp4 file wasn't found."), 500

    try:
        duration = ffprobe_duration(video_path)
        width, height = ffprobe_dimensions(video_path)
    except Exception as e:
        return jsonify(error=f"Could not read downloaded video: {e}"), 400

    JOBS[job_id] = {
        "video_path": video_path,
        "duration": duration,
        "width": width,
        "height": height,
    }
    return jsonify(job_id=job_id, duration=duration, width=width, height=height)


@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("video")
    if not f:
        return jsonify(error="No video file provided"), 400

    job_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(f.filename)[1] or ".mp4"
    video_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
    f.save(video_path)

    try:
        duration = ffprobe_duration(video_path)
        width, height = ffprobe_dimensions(video_path)
    except Exception as e:
        return jsonify(error=f"Could not read video: {e}"), 400

    JOBS[job_id] = {
        "video_path": video_path,
        "duration": duration,
        "width": width,
        "height": height,
    }
    return jsonify(job_id=job_id, duration=duration, width=width, height=height)


@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    api_key = data.get("openai_key", "").strip()
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown job_id"), 404
    if not api_key:
        return jsonify(error="OpenAI API key required for transcription"), 400

    audio_path = os.path.join(TMP_DIR, f"{job_id}.mp3")
    try:
        extract_audio(job["video_path"], audio_path)
        result = call_openai_whisper(audio_path, api_key)
    except Exception as e:
        return jsonify(error=str(e)), 502
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

    words = result.get("words", [])
    segments = result.get("segments", [])
    job["words"] = words
    job["segments"] = segments
    job["transcript_text"] = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}" for s in segments
    )
    return jsonify(segments=segments, word_count=len(words))


@app.route("/api/highlights", methods=["POST"])
def highlights():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    api_key = data.get("anthropic_key", "").strip()
    num_clips = int(data.get("num_clips", 5))
    job = JOBS.get(job_id)
    if not job or "transcript_text" not in job:
        return jsonify(error="Transcribe the video first"), 400
    if not api_key:
        return jsonify(error="Anthropic API key required for highlight selection"), 400

    try:
        picks = call_claude_highlights(job["transcript_text"], job["duration"], api_key, num_clips)
    except Exception as e:
        return jsonify(error=str(e)), 502

    job["highlights"] = picks
    return jsonify(highlights=picks)


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    start = float(data["start"])
    end = float(data["end"])
    title = data.get("title", "clip")
    burn_captions = data.get("captions", True)
    reframe_vertical = data.get("vertical", True)

    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown job_id"), 404

    clip_id = uuid.uuid4().hex[:10]
    video_path = job["video_path"]
    src_w, src_h = job["width"], job["height"]

    raw_cut = os.path.join(TMP_DIR, f"{clip_id}_raw.mp4")
    final_out = os.path.join(CLIPS_DIR, f"{clip_id}.mp4")

    try:
        # 1. Cut the segment (re-encode so the cut is frame-accurate).
        run([
            "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", video_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", raw_cut,
        ])

        vf_parts = []
        if reframe_vertical:
            target_w = round(src_h * 9 / 16)
            target_w = min(target_w, src_w)
            crop_x = compute_smart_crop_x(video_path, start, end, src_w, src_h, target_w)
            vf_parts.append(f"crop={target_w}:{src_h}:{crop_x}:0")
            vf_parts.append("scale=1080:1920")
        else:
            vf_parts.append("scale=1080:-2")

        srt_path = None
        if burn_captions and job.get("words"):
            lines = build_srt_for_window(job["words"], start, end)
            if lines:
                srt_path = os.path.join(TMP_DIR, f"{clip_id}.srt")
                write_srt(lines, srt_path)
                escaped = srt_path.replace(":", r"\:")
                style = (
                    "FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,"
                    "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
                    "Bold=1,Alignment=2,MarginV=90"
                )
                vf_parts.append(f"subtitles={escaped}:force_style='{style}'")

        vf = ",".join(vf_parts)
        run([
            "ffmpeg", "-y", "-i", raw_cut, "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", final_out,
        ])
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        for p in (raw_cut,):
            if os.path.exists(p):
                os.remove(p)

    return jsonify(clip_id=clip_id, title=title, url=f"/clips/{clip_id}.mp4")


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIPS_DIR, filename)


# --------------------------------------------------------------------------
# YouTube OAuth
# --------------------------------------------------------------------------

@app.route("/auth/youtube")
def auth_youtube():
    if not youtube.is_configured():
        return "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — see README.", 400
    return redirect(youtube.get_auth_url())


@app.route("/auth/youtube/callback")
def auth_youtube_callback():
    code = request.args.get("code")
    if not code:
        return "Missing code from Google", 400
    try:
        youtube.handle_callback(code)
    except Exception as e:
        return f"YouTube connection failed: {e}", 500
    return "<script>window.close && window.close();</script>YouTube connected! You can close this tab."


# --------------------------------------------------------------------------
# Instagram OAuth
# --------------------------------------------------------------------------

@app.route("/auth/instagram")
def auth_instagram():
    if not instagram.is_configured():
        return "FB_APP_ID / FB_APP_SECRET not set — see README.", 400
    return redirect(instagram.get_auth_url())


@app.route("/auth/instagram/callback")
def auth_instagram_callback():
    code = request.args.get("code")
    if not code:
        return "Missing code from Facebook", 400
    try:
        instagram.handle_callback(code)
    except Exception as e:
        return f"Instagram connection failed: {e}", 500
    return "<script>window.close && window.close();</script>Instagram connected! You can close this tab."


@app.route("/api/connections")
def api_connections():
    return jsonify(
        youtube_connected=db.get_token("youtube") is not None,
        instagram_connected=db.get_token("instagram") is not None,
        youtube_configured=youtube.is_configured(),
        instagram_configured=instagram.is_configured(),
    )


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------

@app.route("/api/schedule", methods=["POST"])
def api_schedule():
    data = request.get_json(force=True)
    clip_url = data["clip_url"]  # e.g. /clips/abc123.mp4
    clip_path = os.path.join(CLIPS_DIR, os.path.basename(clip_url))
    if not os.path.exists(clip_path):
        return jsonify(error="Clip not found"), 404

    post_id = uuid.uuid4().hex[:12]
    full_url = APP_BASE_URL.rstrip("/") + clip_url
    db.add_scheduled_post(
        post_id=post_id,
        clip_path=clip_path,
        clip_url=full_url,
        title=data.get("title", "Short"),
        caption=data.get("caption", ""),
        platforms=data.get("platforms", []),
        scheduled_time=float(data["scheduled_time"]),  # unix timestamp
    )
    return jsonify(id=post_id)


@app.route("/api/scheduled")
def api_scheduled():
    return jsonify(posts=db.list_scheduled_posts())


@app.route("/cron/publish-due")
def cron_publish_due():
    """Hit this URL from a free external cron service (e.g. cron-job.org)
    every few minutes so scheduled posts still go out even if the free
    hosting instance has spun down and this wakes it back up."""
    results = sched_module.publish_due_posts()
    return jsonify(published=len(results), results=results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
