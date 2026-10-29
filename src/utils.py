"""Small helper functions: validation, date/number formatting and safe text handling.

Everything here is plain Python (no network calls), which keeps it easy to test.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import pandas as pd

NOT_PROVIDED = "Not provided"
UNKNOWN_DATE = "Unknown"

# GitHub usernames: 1-39 characters, letters/digits/single hyphens,
# and they cannot start or end with a hyphen.
_USERNAME_RE = re.compile(r"^(?!-)(?!.*--)[A-Za-z0-9-]{1,39}(?<!-)$")
# Markdown characters are written as HTML entities so untrusted text can never be
# interpreted as Markdown (links, images, emphasis) when it is shown on the page.
_MARKDOWN_ENTITIES = {ord(ch): f"&#{ord(ch)};" for ch in "*_[]()!`~\\"}
_PROFILE_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s?#]+)", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# Username handling
# --------------------------------------------------------------------------- #
def clean_username(raw: Optional[str]) -> str:
    """Tidy what the user typed.

    Accepts "octocat", "@octocat" or "https://github.com/octocat" and returns "octocat".
    """
    name = (raw or "").strip()
    match = _PROFILE_URL_RE.match(name)
    if match:
        name = match.group(1)
    return name.lstrip("@").strip()


def validate_username(raw: Optional[str]) -> tuple[bool, str]:
    """Check a username. Returns (is_valid, message). The message is empty when valid."""
    name = clean_username(raw)
    if not name:
        return False, "Please enter a GitHub username."
    if len(name) > 39:
        return False, "GitHub usernames are at most 39 characters long."
    if not _USERNAME_RE.match(name):
        return False, (
            "Usernames may only contain letters, numbers and single hyphens, "
            "and cannot start or end with a hyphen."
        )
    return True, ""


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def parse_iso_datetime(value: Any) -> Optional[datetime]:
    """Turn a GitHub timestamp such as "2011-01-25T18:44:36Z" into an aware datetime.

    Returns None when the value is missing or not a valid date.
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        else:
            return None
        if pd.isna(parsed):  # pandas NaT
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_date(value: Any, fmt: str = "%d %b %Y") -> str:
    """Format a date like "25 Jan 2011". Returns "Unknown" for missing/invalid values."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return UNKNOWN_DATE
    return parsed.strftime(fmt)


def format_datetime(value: Any) -> str:
    """Format a date and time like "25 Jan 2011, 18:44 UTC"."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return UNKNOWN_DATE
    return parsed.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def relative_time(value: Any, now: Optional[datetime] = None) -> str:
    """Describe how long ago something happened, e.g. "3 days ago"."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return UNKNOWN_DATE
    current = now or datetime.now(timezone.utc)
    seconds = int((current - parsed).total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


# --------------------------------------------------------------------------- #
# Numbers and sizes
# --------------------------------------------------------------------------- #
def format_number(value: Any) -> str:
    """Format an integer with thousands separators ("12,345"). Invalid values become "0"."""
    try:
        if isinstance(value, bool):
            return "0"
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def format_bytes(num_bytes: Any) -> str:
    """Format a byte count as B, KB, MB or GB (base 1024)."""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    if size != size or size < 0:  # NaN or negative
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover - loop always returns


def format_size_kb(kilobytes: Any) -> str:
    """Format a size that GitHub reports in kilobytes, e.g. 2048 -> "2.0 MB"."""
    try:
        return format_bytes(float(kilobytes) * 1024)
    except (TypeError, ValueError):
        return "0 B"


# --------------------------------------------------------------------------- #
# Safe text and URLs (API data is untrusted input)
# --------------------------------------------------------------------------- #
def safe_text(value: Any, max_length: Optional[int] = None) -> str:
    """Make API text safe to place inside HTML.

    Whitespace and line breaks are collapsed into single spaces, the text is
    shortened if needed, and HTML and Markdown special characters are escaped.
    """
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if max_length is not None and len(text) > max_length:
        text = text[: max(max_length - 1, 0)].rstrip() + "…"
    return html.escape(text, quote=True).translate(_MARKDOWN_ENTITIES)


def truncate(value: Any, max_length: int = 100) -> str:
    """Shorten plain text (no HTML escaping) and collapse whitespace."""
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > max_length:
        return text[: max(max_length - 1, 0)].rstrip() + "…"
    return text


def normalize_url(url: Any) -> Optional[str]:
    """Return a safe http(s) URL, or None.

    "example.com" becomes "https://example.com". Dangerous schemes such as
    "javascript:" are rejected.
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or re.search(r"\s", text):
        return None
    if "://" not in text:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text) and not re.match(r"^[^/]+\.[^/]+:\d+", text):
            return None  # e.g. javascript:alert(1), data:..., mailto:...
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return text


def github_url(url: Any) -> Optional[str]:
    """Return the URL only if it points to github.com (used for repository links)."""
    safe = normalize_url(url)
    if not safe:
        return None
    host = (urlparse(safe).hostname or "").lower()
    if host in ("github.com", "www.github.com"):
        return safe
    return None


def avatar_url(url: Any) -> Optional[str]:
    """Return the avatar URL only if it is hosted by GitHub's avatar servers."""
    safe = normalize_url(url)
    if not safe:
        return None
    host = (urlparse(safe).hostname or "").lower()
    if host == "githubusercontent.com" or host.endswith(".githubusercontent.com"):
        return safe
    return None


# --------------------------------------------------------------------------- #
# Optional .env support (tiny loader so we do not need an extra dependency)
# --------------------------------------------------------------------------- #
def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables are never overwritten. Values are never printed.
    """
    try:
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
