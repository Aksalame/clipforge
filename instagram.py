"""Instagram: OAuth via Facebook Login for Business + publishing a Reel via
the Instagram Graph API.

Setup required (see README): a Meta Developer app with Instagram Graph API
product added, and your Instagram account must be a Business or Creator
account linked to a Facebook Page. Reels publishing requires the video to
be reachable at a public URL — we pass your Render app's own /clips/ URL.
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error

import db

FB_APP_ID = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5050")
REDIRECT_URI = f"{APP_BASE_URL}/auth/instagram/callback"
GRAPH_VERSION = "v19.0"

SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,business_management"


def is_configured():
    return bool(FB_APP_ID and FB_APP_SECRET)


def get_auth_url():
    params = {
        "client_id": FB_APP_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "response_type": "code",
    }
    return f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?" + urllib.parse.urlencode(params)


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph API error {e.code}: {e.read().decode(errors='ignore')}")


def _post(url, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Graph API error {e.code}: {e.read().decode(errors='ignore')}")


def handle_callback(code):
    # 1. Exchange code for a short-lived user access token.
    short = _get(f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token", {
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    })

    # 2. Exchange for a long-lived token (~60 days).
    long_lived = _get(f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "fb_exchange_token": short["access_token"],
    })
    user_token = long_lived["access_token"]

    # 3. Find the user's Facebook Page(s) and the linked Instagram Business account.
    pages = _get(f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts", {
        "access_token": user_token,
    })
    if not pages.get("data"):
        raise RuntimeError("No Facebook Page found on this account — Instagram Reels publishing requires a Page linked to an Instagram Business/Creator account.")

    page = pages["data"][0]
    page_token = page["access_token"]
    page_id = page["id"]

    ig = _get(f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}", {
        "fields": "instagram_business_account",
        "access_token": page_token,
    })
    ig_account = ig.get("instagram_business_account")
    if not ig_account:
        raise RuntimeError("This Facebook Page has no linked Instagram Business account. Link one in Instagram app settings first.")

    token_data = {
        "page_token": page_token,
        "page_id": page_id,
        "ig_user_id": ig_account["id"],
        "obtained_at": time.time(),
    }
    db.save_token("instagram", token_data)
    return token_data


def publish_reel(video_url, caption=""):
    token_data = db.get_token("instagram")
    if not token_data:
        raise RuntimeError("Instagram not connected — visit /auth/instagram first")

    ig_user_id = token_data["ig_user_id"]
    page_token = token_data["page_token"]

    # 1. Create a media container.
    container = _post(f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_user_id}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": page_token,
    })
    container_id = container["id"]

    # 2. Poll until Instagram finishes processing the video.
    for _ in range(30):
        status = _get(f"https://graph.facebook.com/{GRAPH_VERSION}/{container_id}", {
            "fields": "status_code",
            "access_token": page_token,
        })
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError("Instagram failed to process the video for the Reel container.")
        time.sleep(5)
    else:
        raise RuntimeError("Timed out waiting for Instagram to process the video.")

    # 3. Publish it.
    result = _post(f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_user_id}/media_publish", {
        "creation_id": container_id,
        "access_token": page_token,
    })
    return {"media_id": result.get("id")}
