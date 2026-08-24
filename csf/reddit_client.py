"""Reddit API client — OAuth, post fetching, comment fetching.

Uses the credentials already in P:/.env (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
REDDIT_USER_AGENT). Handles token refresh, rate limiting, and error recovery.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"

# Conservative: Reddit allows 60/min, we stay well under
REQUEST_DELAY_S = 1.5

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    """Get or refresh the OAuth token."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "ytis/1.0")

    if not client_id or not client_secret:
        raise ValueError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set")

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
    }).encode()

    req = urllib.request.Request(
        REDDIT_TOKEN_URL,
        data=data,
        headers={
            "User-Agent": user_agent,
            "Authorization": f"Basic {urllib.parse.quote(client_id)}:{urllib.parse.quote(client_secret)}",
        },
    )
    # Use basic auth properly
    import base64
    credentials = f"{client_id}:{client_secret}".encode()
    auth_header = base64.b64encode(credentials).decode()
    req = urllib.request.Request(
        REDDIT_TOKEN_URL,
        data=data,
        headers={
            "User-Agent": user_agent,
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())

    token = result.get("access_token")
    if not token:
        raise ValueError(f"Reddit OAuth failed: {result}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + (result.get("expires_in", 3600) - 60)
    return token


def _api_get(endpoint: str, params: dict | None = None) -> dict:
    """Make a GET request to the Reddit API."""
    token = _get_token()
    user_agent = os.environ.get("REDDIT_USER_AGENT", "ytis/1.0")

    url = f"{REDDIT_API_BASE}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Authorization": f"Bearer {token}",
    })

    time.sleep(REQUEST_DELAY_S)  # rate limit

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Rate limited — wait and retry once
            time.sleep(10)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        raise


def fetch_subreddit_posts(
    subreddit: str,
    sort: str = "hot",
    limit: int = 50,
    after: str | None = None,
) -> list[dict]:
    """Fetch posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/ prefix)
        sort: hot, new, top, rising
        limit: Max posts to fetch (max 100 per request)
        after: Pagination token from previous fetch

    Returns:
        List of post dicts with: id, title, selftext, url, score,
        num_comments, author, created_utc, permalink, subreddit
    """
    params = {"limit": min(limit, 100)}
    if after:
        params["after"] = after
    if sort == "top":
        params["t"] = "day"  # top posts from last day

    data = _api_get(f"/r/{subreddit}/{sort}", params)

    posts = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        posts.append({
            "id": d.get("id", ""),
            "name": d.get("name", ""),  # t3_xxx (fullname, for pagination)
            "title": d.get("title", ""),
            "selftext": d.get("selftext", ""),
            "url": d.get("url", ""),
            "score": d.get("score", 0),
            "num_comments": d.get("num_comments", 0),
            "author": d.get("author", ""),
            "created_utc": d.get("created_utc", 0),
            "permalink": f"https://reddit.com{d.get('permalink', '')}",
            "subreddit": d.get("subreddit", subreddit),
            "over_18": d.get("over_18", False),
            "is_self": d.get("is_self", False),
        })

    return posts


def fetch_post_comments(
    subreddit: str,
    post_id: str,
    limit: int = 30,
) -> list[dict]:
    """Fetch top comments for a post.

    Returns list of comment dicts with: id, author, body, score, created_utc
    """
    data = _api_get(f"/r/{subreddit}/comments/{post_id}", {"limit": limit, "sort": "top"})

    comments = []
    # Response is a list: [post_listing, comments_listing]
    if isinstance(data, list) and len(data) > 1:
        for child in data[1].get("data", {}).get("children", []):
            d = child.get("data", {})
            body = d.get("body")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append({
                    "id": d.get("id", ""),
                    "author": d.get("author", ""),
                    "body": body,
                    "score": d.get("score", 0),
                    "created_utc": d.get("created_utc", 0),
                })

    return comments


def post_to_transcript(post: dict, comments: list[dict]) -> str:
    """Convert a post + comments into a transcript-like text blob."""
    parts = []

    # Post title and body
    parts.append(f"Title: {post['title']}")
    parts.append(f"Author: u/{post.get('author', 'unknown')}")
    parts.append(f"Score: {post.get('score', 0)} | Comments: {post.get('num_comments', 0)}")
    parts.append(f"URL: {post.get('permalink', '')}")
    parts.append("")

    if post.get("selftext"):
        parts.append(post["selftext"])
        parts.append("")

    # Top comments
    if comments:
        parts.append(f"--- Top {len(comments)} Comments ---")
        parts.append("")
        for c in comments:
            parts.append(f"[{c['score']} pts] u/{c['author']}:")
            parts.append(c["body"])
            parts.append("")

    return "\n".join(parts)
