"""YouTube: OAuth2 login + uploading a clip as a Short.

Setup required (see README): a Google Cloud project with the YouTube Data
API v3 enabled, an OAuth consent screen, and an OAuth 2.0 Client ID (Web
application) with your redirect URI registered.
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid

import db

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5050")
REDIRECT_URI = f"{APP_BASE_URL}/auth/youtube/callback"

SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def is_configured():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def get_auth_url():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google token error {e.code}: {e.read().decode(errors='ignore')}")


def handle_callback(code):
    tokens = _post_form("https://oauth2.googleapis.com/token", {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    tokens["obtained_at"] = time.time()
    db.save_token("youtube", tokens)
    return tokens


def _refresh_access_token(token_data):
    fresh = _post_form("https://oauth2.googleapis.com/token", {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": token_data["refresh_token"],
        "grant_type": "refresh_token",
    })
    token_data["access_token"] = fresh["access_token"]
    token_data["obtained_at"] = time.time()
    token_data["expires_in"] = fresh.get("expires_in", 3600)
    db.save_token("youtube", token_data)
    return token_data


def get_valid_access_token():
    token_data = db.get_token("youtube")
    if not token_data:
        return None
    age = time.time() - token_data.get("obtained_at", 0)
    if age > token_data.get("expires_in", 3600) - 60:
        token_data = _refresh_access_token(token_data)
    return token_data["access_token"]


def upload_short(video_path, title, description="", privacy_status="public"):
    """Multipart upload to YouTube Data API v3. Fine for short clips
    (<~100MB); for larger files a resumable upload session would be needed."""
    access_token = get_valid_access_token()
    if not access_token:
        raise RuntimeError("YouTube not connected — visit /auth/youtube first")

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "22",
        },
        "status": {"privacyStatus": privacy_status},
    }

    boundary = uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
    body += json.dumps(metadata).encode()
    body += f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
    with open(video_path, "rb") as f:
        body += f.read()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"YouTube upload error {e.code}: {e.read().decode(errors='ignore')}")

    return {"video_id": result.get("id"), "url": f"https://youtube.com/shorts/{result.get('id')}"}
