"""Data analysis helpers: statistics, language aggregation, insights and event summaries.

These functions take plain Python data (what the GitHub API returned) and return
Pandas DataFrames or dictionaries. They never call the network, so they are easy to test.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from src.utils import parse_iso_datetime, truncate

NO_LANGUAGE = "Not specified"
OTHER_LABEL = "Other"

REPO_COLUMNS = [
    "name", "description", "language", "stars", "forks", "open_issues", "size_kb",
    "created_at", "updated_at", "pushed_at", "visibility", "is_fork", "archived", "url",
]
LANGUAGE_COLUMNS = ["language", "bytes", "percentage", "repos"]
EVENT_COLUMNS = ["created_at", "type", "event_type", "repository", "details"]

# Sort option -> (column, ascending by default)
SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    "Stars": ("stars", False),
    "Forks": ("forks", False),
    "Name": ("name", True),
    "Last updated": ("updated_at", False),
}
REPO_TYPE_OPTIONS = ["All", "Original", "Forks"]


# --------------------------------------------------------------------------- #
# Repositories -> DataFrame
# --------------------------------------------------------------------------- #
def _as_count(value: Any) -> int:
    """Convert API numbers to a non-negative int (missing/invalid values become 0)."""
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def repos_to_dataframe(repos: Optional[Iterable[Mapping[str, Any]]]) -> pd.DataFrame:
    """Turn the raw repository list from the API into a clean DataFrame."""
    rows = []
    for repo in repos or []:
        if not isinstance(repo, Mapping) or not repo.get("name"):
            continue
        visibility = repo.get("visibility") or ("private" if repo.get("private") else "public")
        rows.append(
            {
                "name": str(repo["name"]),
                "description": str(repo.get("description") or "").strip(),
                "language": str(repo.get("language") or NO_LANGUAGE),
                "stars": _as_count(repo.get("stargazers_count")),
                "forks": _as_count(repo.get("forks_count")),
                "open_issues": _as_count(repo.get("open_issues_count")),
                "size_kb": _as_count(repo.get("size")),
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "pushed_at": repo.get("pushed_at"),
                "visibility": str(visibility),
                "is_fork": bool(repo.get("fork")),
                "archived": bool(repo.get("archived")),
                "url": str(repo.get("html_url") or ""),
            }
        )

    df = pd.DataFrame(rows, columns=REPO_COLUMNS)
    for column in ("created_at", "updated_at", "pushed_at"):
        df[column] = pd.to_datetime(df[column], errors="coerce", utc=True, format="ISO8601")
    for column in ("stars", "forks", "open_issues", "size_kb"):
        df[column] = df[column].astype("int64")
    for column in ("is_fork", "archived"):
        df[column] = df[column].astype(bool)
    return df


def compute_totals(df: pd.DataFrame) -> dict[str, int]:
    """Totals across the repositories that were actually fetched."""
    if df.empty:
        return {
            "repo_count": 0, "stars": 0, "forks": 0, "open_issues": 0,
            "size_kb": 0, "fork_repos": 0, "original_repos": 0, "archived_repos": 0,
        }
    fork_repos = int(df["is_fork"].sum())
    return {
        "repo_count": int(len(df)),
        "stars": int(df["stars"].sum()),
        "forks": int(df["forks"].sum()),
        "open_issues": int(df["open_issues"].sum()),
        "size_kb": int(df["size_kb"].sum()),
        "fork_repos": fork_repos,
        "original_repos": int(len(df)) - fork_repos,
        "archived_repos": int(df["archived"].sum()),
    }


# --------------------------------------------------------------------------- #
# Filtering and sorting
# --------------------------------------------------------------------------- #
def language_options(df: pd.DataFrame) -> list[str]:
    """Sorted list of primary languages found in the repositories."""
    if df.empty:
        return []
    return sorted(df["language"].unique().tolist(), key=str.lower)


def filter_repos(
    df: pd.DataFrame,
    name_query: str = "",
    language: Optional[str] = None,
    repo_type: str = "All",
) -> pd.DataFrame:
    """Filter by (part of) the name, primary language and original/fork type."""
    result = df
    query = (name_query or "").strip()
    if query:
        mask = result["name"].str.contains(query, case=False, regex=False, na=False)
        result = result[mask]
    if language and language != "All":
        result = result[result["language"] == language]
    if repo_type == "Original":
        result = result[~result["is_fork"]]
    elif repo_type == "Forks":
        result = result[result["is_fork"]]
    return result


def sort_repos(df: pd.DataFrame, sort_by: str = "Stars", reverse: bool = False) -> pd.DataFrame:
    """Sort repositories by "Stars", "Forks", "Name" or "Last updated".

    Each option has a natural direction (most stars first, A-Z for names, newest first).
    Set `reverse=True` to flip it. Missing dates are always placed last.
    """
    if sort_by not in SORT_OPTIONS:
        raise ValueError(f"Unknown sort option: {sort_by!r}. Choose from {list(SORT_OPTIONS)}.")
    column, ascending = SORT_OPTIONS[sort_by]
    if reverse:
        ascending = not ascending
    if df.empty:
        return df
    key = (lambda series: series.str.lower()) if column == "name" else None
    return df.sort_values(
        by=column, ascending=ascending, kind="mergesort", na_position="last", key=key
    )


def select_repos_for_language_analysis(
    df: pd.DataFrame, limit: int, include_forks: bool = False
) -> list[str]:
    """Choose which repositories to request language data for.

    One API request is needed per repository, so we analyse the most recently
    pushed repositories first, up to `limit`.
    """
    if df.empty or limit <= 0:
        return []
    candidates = df if include_forks else df[~df["is_fork"]]
    candidates = candidates.sort_values(
        by="pushed_at", ascending=False, kind="mergesort", na_position="last"
    )
    return candidates["name"].head(limit).tolist()


# --------------------------------------------------------------------------- #
# Programming languages
# --------------------------------------------------------------------------- #
def _empty_language_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "language": pd.Series(dtype="object"),
            "bytes": pd.Series(dtype="int64"),
            "percentage": pd.Series(dtype="float64"),
            "repos": pd.Series(dtype="int64"),
        },
        columns=LANGUAGE_COLUMNS,
    )


def _valid_language_map(language_map: Any) -> dict[str, int]:
    """Keep only entries that look like {"Python": 1234} with a positive byte count."""
    if not isinstance(language_map, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for language, size in language_map.items():
        if not isinstance(language, str) or not language:
            continue
        if isinstance(size, bool) or not isinstance(size, (int, float)):
            continue
        if size != size or size <= 0:  # NaN or non-positive
            continue
        cleaned[language] = int(size)
    return cleaned


def aggregate_languages(language_maps: Iterable[Any]) -> pd.DataFrame:
    """Add up language bytes across repositories.

    Returns columns: language, bytes, percentage (0-100), repos (how many repositories
    contain that language), sorted from most to least bytes.
    """
    byte_totals: Counter = Counter()
    repo_counts: Counter = Counter()
    for language_map in language_maps:
        for language, size in _valid_language_map(language_map).items():
            byte_totals[language] += size
            repo_counts[language] += 1

    total = sum(byte_totals.values())
    if total == 0:
        return _empty_language_frame()

    ordered = sorted(byte_totals.items(), key=lambda item: (-item[1], item[0].lower()))
    df = pd.DataFrame(ordered, columns=["language", "bytes"])
    df["percentage"] = df["bytes"] / total * 100
    df["repos"] = df["language"].map(repo_counts).astype("int64")
    return df[LANGUAGE_COLUMNS]


def group_small_languages(language_df: pd.DataFrame, top_n: int = 8) -> pd.DataFrame:
    """Keep the `top_n` languages and combine the rest into one "Other" row (for pie charts)."""
    if len(language_df) <= top_n:
        return language_df.copy()
    top = language_df.head(top_n)
    rest = language_df.iloc[top_n:]
    other = pd.DataFrame(
        [
            {
                "language": OTHER_LABEL,
                "bytes": int(rest["bytes"].sum()),
                "percentage": float(rest["percentage"].sum()),
                "repos": int(rest["repos"].max()),
            }
        ]
    )
    return pd.concat([top, other], ignore_index=True)


def count_repos_without_languages(language_maps: Mapping[str, Any]) -> int:
    """How many analysed repositories had no detectable language data."""
    return sum(1 for language_map in language_maps.values() if not _valid_language_map(language_map))


# --------------------------------------------------------------------------- #
# Repository insights
# --------------------------------------------------------------------------- #
def _row_summary(row: pd.Series) -> dict[str, Any]:
    return {
        "name": row["name"],
        "url": row["url"],
        "stars": int(row["stars"]),
        "forks": int(row["forks"]),
        "language": row["language"],
        "updated_at": row["updated_at"],
        "description": row["description"],
    }


def language_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Repositories grouped by their primary language (uses data we already have)."""
    columns = ["language", "repos", "stars", "forks"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = (
        df.groupby("language", as_index=False)
        .agg(repos=("name", "count"), stars=("stars", "sum"), forks=("forks", "sum"))
    )
    grouped = grouped.sort_values(
        by=["repos", "language"], ascending=[False, True], kind="mergesort"
    )
    return grouped.reset_index(drop=True)


def repo_insights(df: pd.DataFrame, recent_count: int = 5) -> dict[str, Any]:
    """Facts that can be read directly from the fetched repositories.

    "most_starred" / "most_forked" are None when no repository has any stars / forks,
    because naming a "winner" with zero would not be a meaningful conclusion.
    """
    insights: dict[str, Any] = {
        "most_starred": None,
        "most_forked": None,
        "recently_updated": [],
        "without_description": [],
        "original_count": 0,
        "fork_count": 0,
        "archived_count": 0,
        "by_language": language_summary(df),
    }
    if df.empty:
        return insights

    if df["stars"].max() > 0:
        insights["most_starred"] = _row_summary(df.loc[df["stars"].idxmax()])
    if df["forks"].max() > 0:
        insights["most_forked"] = _row_summary(df.loc[df["forks"].idxmax()])

    dated = df.dropna(subset=["updated_at"]).sort_values(
        by="updated_at", ascending=False, kind="mergesort"
    )
    insights["recently_updated"] = [_row_summary(row) for _, row in dated.head(recent_count).iterrows()]

    missing = df[df["description"].str.strip() == ""]
    insights["without_description"] = [_row_summary(row) for _, row in missing.iterrows()]

    totals = compute_totals(df)
    insights["original_count"] = totals["original_repos"]
    insights["fork_count"] = totals["fork_repos"]
    insights["archived_count"] = totals["archived_repos"]
    return insights


# --------------------------------------------------------------------------- #
# Public events (Events API)
# --------------------------------------------------------------------------- #
EVENT_LABELS = {
    "PushEvent": "Push",
    "CreateEvent": "Create",
    "DeleteEvent": "Delete",
    "WatchEvent": "Star",
    "ForkEvent": "Fork",
    "IssuesEvent": "Issue",
    "IssueCommentEvent": "Issue comment",
    "PullRequestEvent": "Pull request",
    "PullRequestReviewEvent": "PR review",
    "PullRequestReviewCommentEvent": "PR review comment",
    "ReleaseEvent": "Release",
    "PublicEvent": "Made public",
    "MemberEvent": "Collaborator",
    "CommitCommentEvent": "Commit comment",
    "GollumEvent": "Wiki edit",
}


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _branch_name(ref: Any) -> str:
    if isinstance(ref, str) and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return ref if isinstance(ref, str) else ""


def _titled(prefix: str, number: Any, title: Any) -> str:
    text = prefix
    if number:
        text += f" #{number}"
    if title:
        text += f": {truncate(title, 80)}"
    return text


def describe_event(event: Mapping[str, Any]) -> str:
    """A short, human-readable description of what happened in one public event."""
    event_type = event.get("type")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    action = str(payload.get("action") or "").capitalize()

    if event_type == "PushEvent":
        commits = payload.get("commits")
        count = payload.get("size")
        if not isinstance(count, int) or isinstance(count, bool):
            count = len(commits) if isinstance(commits, list) else None
        text = f"Pushed {_plural(count, 'commit')}" if count is not None else "Pushed commits"
        branch = _branch_name(payload.get("ref"))
        if branch:
            text += f" to {truncate(branch, 40)}"
        if isinstance(commits, list) and commits and isinstance(commits[-1], Mapping):
            message = truncate((commits[-1].get("message") or "").split("\n")[0], 70)
            if message:
                text += f' - "{message}"'
        return text
    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type") or "item"
        ref = payload.get("ref")
        return f"Created {ref_type}" + (f" '{truncate(ref, 40)}'" if ref else "")
    if event_type == "DeleteEvent":
        ref_type = payload.get("ref_type") or "item"
        ref = payload.get("ref")
        return f"Deleted {ref_type}" + (f" '{truncate(ref, 40)}'" if ref else "")
    if event_type == "WatchEvent":
        return "Starred the repository"
    if event_type == "ForkEvent":
        forkee = payload.get("forkee")
        full_name = forkee.get("full_name") if isinstance(forkee, Mapping) else None
        return f"Forked to {truncate(full_name, 60)}" if full_name else "Forked the repository"
    if event_type == "IssuesEvent":
        issue = payload.get("issue") if isinstance(payload.get("issue"), Mapping) else {}
        return _titled(f"{action or 'Updated'} issue", issue.get("number"), issue.get("title"))
    if event_type == "PullRequestEvent":
        pull = payload.get("pull_request") if isinstance(payload.get("pull_request"), Mapping) else {}
        verb = "Merged" if action == "Closed" and pull.get("merged") else (action or "Updated")
        return _titled(f"{verb} pull request", payload.get("number") or pull.get("number"), pull.get("title"))
    if event_type == "IssueCommentEvent":
        issue = payload.get("issue") if isinstance(payload.get("issue"), Mapping) else {}
        return _titled("Commented on", issue.get("number"), issue.get("title"))
    if event_type in ("PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
        pull = payload.get("pull_request") if isinstance(payload.get("pull_request"), Mapping) else {}
        return _titled("Reviewed pull request", pull.get("number"), pull.get("title"))
    if event_type == "ReleaseEvent":
        release = payload.get("release") if isinstance(payload.get("release"), Mapping) else {}
        tag = release.get("tag_name")
        return f"{action or 'Published'} release {truncate(tag, 40)}".strip() if tag else "Published a release"
    if event_type == "PublicEvent":
        return "Made the repository public"
    if event_type == "MemberEvent":
        member = payload.get("member") if isinstance(payload.get("member"), Mapping) else {}
        login = member.get("login")
        return f"{action or 'Updated'} collaborator {truncate(login, 40)}".strip() if login else "Changed collaborators"
    if event_type == "CommitCommentEvent":
        return "Commented on a commit"
    if event_type == "GollumEvent":
        return "Edited wiki pages"
    return "No further details available"


def summarize_events(events: Optional[Iterable[Any]], limit: int = 50) -> pd.DataFrame:
    """Turn raw events into a table: created_at, type, event_type, repository, details."""
    rows = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        repo = event.get("repo")
        repo_name = repo.get("name") if isinstance(repo, Mapping) else None
        raw_type = str(event.get("type") or "Unknown")
        rows.append(
            {
                "created_at": parse_iso_datetime(event.get("created_at")),
                "type": EVENT_LABELS.get(raw_type, raw_type.replace("Event", "") or "Unknown"),
                "event_type": raw_type,
                "repository": str(repo_name) if repo_name else "Unknown",
                "details": describe_event(event),
            }
        )
        if len(rows) >= limit:
            break
    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    return df


def event_type_counts(events_df: pd.DataFrame) -> pd.DataFrame:
    """How many events of each type were found (for a small bar chart)."""
    if events_df.empty:
        return pd.DataFrame(columns=["type", "count"])
    counts = events_df["type"].value_counts().reset_index()
    counts.columns = ["type", "count"]
    return counts
