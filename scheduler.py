"""Publishes any scheduled posts whose time has arrived. Called both by an
in-process APScheduler loop (works while the app is awake) and by an
external cron ping to /cron/publish-due (works around free-tier hosts that
spin down when idle — see README)."""

import json
import logging

import db
import youtube
import instagram

logger = logging.getLogger(__name__)


def publish_due_posts():
    due = db.get_due_posts()
    results = []
    for post in due:
        platforms = json.loads(post["platforms"])
        outcome = {"id": post["id"], "platforms": {}}
        any_success = False

        if "youtube" in platforms:
            try:
                r = youtube.upload_short(post["clip_path"], post["title"] or "Short", post["caption"] or "")
                outcome["platforms"]["youtube"] = {"ok": True, **r}
                any_success = True
            except Exception as e:
                outcome["platforms"]["youtube"] = {"ok": False, "error": str(e)}
                logger.exception("YouTube publish failed for %s", post["id"])

        if "instagram" in platforms:
            try:
                r = instagram.publish_reel(post["clip_url"], post["caption"] or "")
                outcome["platforms"]["instagram"] = {"ok": True, **r}
                any_success = True
            except Exception as e:
                outcome["platforms"]["instagram"] = {"ok": False, "error": str(e)}
                logger.exception("Instagram publish failed for %s", post["id"])

        status = "done" if any_success else "failed"
        db.mark_post_status(post["id"], status, outcome)
        results.append(outcome)
    return results


def start_background_scheduler():
    """Optional in-process scheduler — only fires while the app is awake."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler not installed; relying on external cron ping only")
        return None

    sched = BackgroundScheduler()
    sched.add_job(publish_due_posts, "interval", minutes=1, id="publish_due")
    sched.start()
    return sched
