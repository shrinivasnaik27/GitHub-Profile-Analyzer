"""GitHub REST API access: requests, pagination, error handling and caching.

The first half of this file is plain Python (easy to test with a fake session).
The second half wraps those functions with Streamlit's cache so repeated
searches do not use up the API rate limit.

The official REST API is used for everything - no web scraping.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

import requests
import streamlit as st

from src.utils import load_env_file

API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15          # seconds
PER_PAGE = 100                # maximum allowed by GitHub
MAX_REPO_PAGES = 5            # at most 500 repositories are fetched
EVENTS_PER_PAGE = 100         # one page of recent public events

# Error categories used by the user interface.
NOT_FOUND = "not_found"
RATE_LIMIT = "rate_limit"
NETWORK = "network"
AUTH = "auth"
FORBIDDEN = "forbidden"
SERVER = "server"
INVALID = "invalid_response"
HTTP = "http"

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class GitHubAPIError(Exception):
    """Raised when a GitHub API request fails. `kind` says what went wrong."""

    def __init__(self, kind: str, message: str, reset_at: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.reset_at = reset_at  # Unix time when the rate limit resets, if known


# --------------------------------------------------------------------------- #
# Low-level request helper
# --------------------------------------------------------------------------- #
def _headers(token: Optional[str]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitHub-Profile-Analyzer",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_message_from(response: Any) -> str:
    """Read GitHub's "message" field from an error response, if there is one."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("message", ""))
    return ""


def _is_rate_limited(response: Any) -> bool:
    if response.status_code == 429:
        return True
    if str(response.headers.get("X-RateLimit-Remaining", "")) == "0":
        return True
    return "rate limit" in _error_message_from(response).lower()


def _rate_limit_error(response: Any) -> GitHubAPIError:
    reset_at = _int_or_none(response.headers.get("X-RateLimit-Reset"))
    retry_after = _int_or_none(response.headers.get("Retry-After"))
    if reset_at is None and retry_after is not None:
        reset_at = int(time.time()) + retry_after

    when = ""
    if reset_at:
        minutes = max(1, round((reset_at - time.time()) / 60))
        reset_time = datetime.fromtimestamp(reset_at, tz=timezone.utc).strftime("%H:%M UTC")
        when = f" The limit resets in about {minutes} minute(s) (at {reset_time})."
    return GitHubAPIError(
        RATE_LIMIT,
        "GitHub's API rate limit has been reached." + when
        + " You can wait and try again, or add a GitHub token (see the README) for a higher limit.",
        reset_at=reset_at,
    )


def _request(
    path: str,
    params: Optional[dict[str, Any]] = None,
    token: Optional[str] = None,
    session: Any = None,
) -> Any:
    """Send one GET request and return the parsed JSON. Raises GitHubAPIError on failure."""
    client = session if session is not None else requests
    url = f"{API_BASE}{path}"
    try:
        response = client.get(
            url, headers=_headers(token), params=params, timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.Timeout as exc:
        raise GitHubAPIError(
            NETWORK, "The request to GitHub timed out. Please try again in a moment."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise GitHubAPIError(
            NETWORK, "Could not reach GitHub. Check your internet connection and try again."
        ) from exc

    status = response.status_code
    if status == 200:
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubAPIError(
                INVALID, "GitHub returned a response that could not be read."
            ) from exc
    if status == 404:
        raise GitHubAPIError(NOT_FOUND, "The requested GitHub resource was not found.")
    if status in (403, 429):
        if _is_rate_limited(response):
            raise _rate_limit_error(response)
        raise GitHubAPIError(
            FORBIDDEN, "GitHub refused the request (HTTP 403). Please try again later."
        )
    if status == 401:
        raise GitHubAPIError(
            AUTH,
            "GitHub rejected the configured token (HTTP 401). "
            "Remove it or replace it with a valid one.",
        )
    if status >= 500:
        raise GitHubAPIError(
            SERVER, f"GitHub is having problems right now (HTTP {status}). Try again later."
        )
    raise GitHubAPIError(HTTP, f"Unexpected response from GitHub (HTTP {status}).")


# --------------------------------------------------------------------------- #
# Endpoint functions (plain, uncached)
# --------------------------------------------------------------------------- #
def fetch_user(username: str, token: Optional[str] = None, session: Any = None) -> dict:
    """GET /users/{username}"""
    try:
        data = _request(f"/users/{quote(username, safe='')}", token=token, session=session)
    except GitHubAPIError as exc:
        if exc.kind == NOT_FOUND:
            raise GitHubAPIError(
                NOT_FOUND,
                f"No GitHub user named '{username}' was found. "
                "Check the spelling and try again.",
            ) from exc
        raise
    if not isinstance(data, dict) or not data.get("login"):
        raise GitHubAPIError(INVALID, "GitHub returned unexpected profile data.")
    return data


def fetch_repos(
    username: str,
    token: Optional[str] = None,
    session: Any = None,
    max_pages: int = MAX_REPO_PAGES,
) -> tuple[list[dict], bool]:
    """GET /users/{username}/repos (paginated).

    Returns (repositories, limit_reached). `limit_reached` is True when we stopped at
    `max_pages` full pages, so the user may have more repositories than were fetched.
    """
    repos: list[dict] = []
    limit_reached = False
    for page in range(1, max_pages + 1):
        data = _request(
            f"/users/{quote(username, safe='')}/repos",
            params={
                "per_page": PER_PAGE,
                "page": page,
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
            },
            token=token,
            session=session,
        )
        if not isinstance(data, list):
            raise GitHubAPIError(INVALID, "GitHub returned unexpected repository data.")
        repos.extend(item for item in data if isinstance(item, dict) and item.get("name"))
        if len(data) < PER_PAGE:
            break
    else:
        limit_reached = True
    return repos, limit_reached


def fetch_repo_languages(
    owner: str, repo: str, token: Optional[str] = None, session: Any = None
) -> dict[str, int]:
    """GET /repos/{owner}/{repo}/languages -> {"Python": 12345, ...} (bytes of code)."""
    data = _request(
        f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/languages",
        token=token,
        session=session,
    )
    if not isinstance(data, dict):
        raise GitHubAPIError(INVALID, "GitHub returned unexpected language data.")
    cleaned: dict[str, int] = {}
    for language, size in data.items():
        if isinstance(language, str) and isinstance(size, int) and not isinstance(size, bool) and size > 0:
            cleaned[language] = size
    return cleaned


def fetch_events(
    username: str, token: Optional[str] = None, session: Any = None
) -> list[dict]:
    """GET /users/{username}/events/public (one page of recent public events)."""
    data = _request(
        f"/users/{quote(username, safe='')}/events/public",
        params={"per_page": EVENTS_PER_PAGE},
        token=token,
        session=session,
    )
    if not isinstance(data, list):
        raise GitHubAPIError(INVALID, "GitHub returned unexpected event data.")
    return [item for item in data if isinstance(item, dict)]


def fetch_rate_limit(token: Optional[str] = None, session: Any = None) -> Optional[dict]:
    """GET /rate_limit (this call does not count against the limit).

    Returns {"limit", "remaining", "reset"} or None if it cannot be read.
    """
    try:
        data = _request("/rate_limit", token=token, session=session)
    except GitHubAPIError:
        return None
    try:
        core = data["resources"]["core"]
        return {
            "limit": int(core["limit"]),
            "remaining": int(core["remaining"]),
            "reset": int(core["reset"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Fetching languages for many repositories
# --------------------------------------------------------------------------- #
@dataclass
class LanguageFetchResult:
    """Outcome of fetching language data for several repositories."""

    languages: dict[str, dict[str, int]] = field(default_factory=dict)  # repo -> {language: bytes}
    failed: list[str] = field(default_factory=list)   # repositories that returned an error
    skipped: int = 0                                  # repositories not attempted (rate limit)
    rate_limited: bool = False


def fetch_languages_for_repos(
    owner: str,
    repo_names: list[str],
    fetch_one: Callable[[str, str], dict[str, int]],
    progress: Optional[Callable[[int, int], None]] = None,
) -> LanguageFetchResult:
    """Fetch language bytes for each repository, stopping politely at a rate limit.

    `fetch_one(owner, repo)` does the real request (cached in the app, faked in tests).
    `progress(done, total)` is called after each repository, if given.
    """
    result = LanguageFetchResult()
    total = len(repo_names)
    for index, name in enumerate(repo_names):
        try:
            result.languages[name] = fetch_one(owner, name)
        except GitHubAPIError as exc:
            if exc.kind == RATE_LIMIT:
                result.rate_limited = True
                result.skipped = total - index
                break
            result.failed.append(name)
        if progress:
            progress(index + 1, total)
    return result


# --------------------------------------------------------------------------- #
# Token handling (optional) - the token is never printed or stored in the UI
# --------------------------------------------------------------------------- #
def get_token() -> Optional[str]:
    """Look for an optional token in the environment, a .env file or Streamlit secrets."""
    load_env_file(ENV_FILE)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        try:
            token = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
        except Exception:  # no secrets file configured - that is fine
            token = ""
    return token or None


# --------------------------------------------------------------------------- #
# Cached wrappers used by the Streamlit app
# A leading underscore (_token) tells Streamlit not to hash that argument.
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=600, show_spinner=False)
def cached_user(username: str, _token: Optional[str] = None) -> dict:
    return fetch_user(username, _token)


@st.cache_data(ttl=600, show_spinner=False)
def cached_repos(username: str, _token: Optional[str] = None) -> tuple[list[dict], bool]:
    return fetch_repos(username, _token)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_repo_languages(owner: str, repo: str, _token: Optional[str] = None) -> dict[str, int]:
    return fetch_repo_languages(owner, repo, _token)


@st.cache_data(ttl=300, show_spinner=False)
def cached_events(username: str, _token: Optional[str] = None) -> list[dict]:
    return fetch_events(username, _token)


@st.cache_data(ttl=60, show_spinner=False)
def cached_rate_limit(_token: Optional[str] = None) -> Optional[dict]:
    return fetch_rate_limit(_token)


def clear_cache() -> None:
    """Forget all cached API responses (used by the Refresh button)."""
    st.cache_data.clear()
