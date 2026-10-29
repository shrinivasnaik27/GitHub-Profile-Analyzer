"""GitHub Profile Analyzer - Streamlit interface.

Run with:  streamlit run app.py

This file only builds the page. API calls live in src/github_api.py,
calculations in src/analytics.py and small helpers in src/utils.py.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics import (
    OTHER_LABEL,
    REPO_TYPE_OPTIONS,
    SORT_OPTIONS,
    aggregate_languages,
    compute_totals,
    count_repos_without_languages,
    event_type_counts,
    filter_repos,
    group_small_languages,
    language_options,
    repo_insights,
    repos_to_dataframe,
    select_repos_for_language_analysis,
    sort_repos,
    summarize_events,
)
from src.github_api import (
    MAX_REPO_PAGES,
    NOT_FOUND,
    RATE_LIMIT,
    GitHubAPIError,
    LanguageFetchResult,
    cached_events,
    cached_rate_limit,
    cached_repo_languages,
    cached_repos,
    cached_user,
    clear_cache,
    fetch_languages_for_repos,
    get_token,
)
from src.utils import (
    NOT_PROVIDED,
    avatar_url,
    clean_username,
    format_bytes,
    format_date,
    format_datetime,
    format_number,
    format_size_kb,
    github_url,
    normalize_url,
    relative_time,
    safe_text,
    validate_username,
)

ACCENT = "#0F766E"
PALETTE = ["#0F766E", "#1E3A5F", "#C2410C", "#6D28D9", "#0369A1",
           "#A16207", "#BE185D", "#4D7C0F", "#475569"]
EXAMPLE_USERNAMES = ["octocat", "torvalds", "gvanrossum"]
REPO_STATE_KEYS = ["repo_query", "repo_language", "repo_type", "repo_sort",
                   "repo_reverse", "repo_page_size"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
    font-family: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
}
.block-container { padding-top: 2.2rem; max-width: 1200px; }
h1, h2, h3 { letter-spacing: -0.01em; }
[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #D9E0E8; border-radius: 8px; padding: 14px 18px;
}
[data-testid="stMetricValue"] { font-weight: 600; }
.avatar { width: 150px; height: 150px; border-radius: 50%; border: 3px solid #FFFFFF;
          box-shadow: 0 0 0 1px #D9E0E8; display: block; }
.profile-name { font-size: 1.85rem; font-weight: 600; line-height: 1.2; }
.profile-login { font-size: 1rem; margin: 2px 0 10px 0; }
.profile-login a { color: #0F766E; text-decoration: none; }
.profile-bio { font-size: 1.02rem; margin-bottom: 14px; max-width: 70ch; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px 24px; }
.fact-label { display: block; font-size: 0.8rem; color: #5B6B7F; }
.fact-value { display: block; font-size: 0.95rem; overflow-wrap: anywhere; }
.fact-value a { color: #0F766E; }
.muted { color: #8896A8; }
@media (max-width: 640px) { .avatar { width: 110px; height: 110px; } }
</style>
"""


# --------------------------------------------------------------------------- #
# Small presentation helpers
# --------------------------------------------------------------------------- #
def md_escape(text: Any) -> str:
    """Escape Markdown characters so untrusted text is shown literally."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$])", r"\\\1", str(text or ""))


def style_figure(fig, height: int = 380):
    """Shared look for all Plotly charts."""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans, Segoe UI, system-ui, sans-serif", color="#14213D"),
        legend=dict(title=None),
    )
    return fig


def show_chart(fig, key: str) -> None:
    st.plotly_chart(fig, width="stretch", key=key, config={"displayModeBar": False})


def fact_html(label: str, value_html: str) -> str:
    return (f'<div><span class="fact-label">{label}</span>'
            f'<span class="fact-value">{value_html}</span></div>')


def text_or_not_provided(value: Any) -> str:
    text = safe_text(value, 120)
    return text if text else f'<span class="muted">{NOT_PROVIDED}</span>'


def epoch_to_text(epoch: int) -> str:
    return format_datetime(datetime.fromtimestamp(epoch, tz=timezone.utc))


# --------------------------------------------------------------------------- #
# Session state and analysis
# --------------------------------------------------------------------------- #
def init_state() -> None:
    for key, default in (("analysis", None), ("error", None), ("pending", None),
                         ("last_request", None)):
        st.session_state.setdefault(key, default)


def request_example(username: str) -> None:
    """Callback for the example buttons."""
    st.session_state["username_input"] = username
    st.session_state["pending"] = {
        "username": username,
        "include_forks": bool(st.session_state.get("opt_include_forks", False)),
        "max_repos": int(st.session_state.get("opt_max_repos", 20)),
    }


def request_refresh() -> None:
    """Callback for the Refresh button: clear cached API data and run the same search again."""
    last = st.session_state.get("last_request")
    if last:
        clear_cache()
        st.session_state["pending"] = dict(last)


def request_new_search() -> None:
    """Callback for the New search button."""
    st.session_state["analysis"] = None
    st.session_state["error"] = None
    st.session_state["last_request"] = None
    st.session_state["username_input"] = ""


def run_analysis(username: str, include_forks: bool, max_repos: int) -> None:
    """Fetch everything for one user and store the result in session state."""
    token = get_token()
    st.session_state["last_request"] = {
        "username": username, "include_forks": include_forks, "max_repos": max_repos}
    st.session_state["analysis"] = None
    st.session_state["error"] = None
    for key in REPO_STATE_KEYS:
        st.session_state.pop(key, None)

    failure: Optional[GitHubAPIError] = None
    slot = st.empty()
    with slot.container():
        with st.status(f"Analyzing {username}", expanded=True):
            try:
                st.write("Fetching profile...")
                profile = cached_user(username.lower(), token)
                login = profile["login"]

                st.write("Fetching public repositories...")
                repos, limit_reached = cached_repos(login, token)
                repos_df = repos_to_dataframe(repos)

                names = select_repos_for_language_analysis(repos_df, max_repos, include_forks)
                eligible = len(repos_df) if include_forks else int((~repos_df["is_fork"]).sum())
                language_result = LanguageFetchResult()
                if names:
                    st.write(f"Fetching language data for up to {len(names)} repositories...")
                    bar = st.progress(0.0, text="Starting...")

                    def on_progress(done: int, total: int) -> None:
                        bar.progress(done / total, text=f"Language data: {done} of {total} repositories")

                    language_result = fetch_languages_for_repos(
                        login, names,
                        lambda owner, repo: cached_repo_languages(owner, repo, token),
                        on_progress,
                    )

                st.write("Fetching recent public events...")
                events: list[dict] = []
                events_error: Optional[str] = None
                try:
                    events = cached_events(login, token)
                except GitHubAPIError as exc:  # events are optional - keep going
                    events_error = exc.message
            except GitHubAPIError as exc:
                failure = exc

    slot.empty()  # remove the progress box once we are done (success or failure)
    if failure is not None:
        st.session_state["error"] = failure
        return

    cached_rate_limit.clear()  # so the sidebar shows fresh numbers
    st.session_state["analysis"] = {
        "profile": profile,
        "repos_df": repos_df,
        "limit_reached": limit_reached,
        "language_result": language_result,
        "eligible_repos": eligible,
        "include_forks": include_forks,
        "max_repos": max_repos,
        "events": events,
        "events_error": events_error,
        "fetched_at": datetime.now(timezone.utc),
    }
    st.toast(f"Loaded @{login}")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_search_form() -> None:
    with st.sidebar:
        st.markdown("## GitHub Profile Analyzer")
        st.caption("Explore any public GitHub profile.")
        with st.form("search_form"):
            st.text_input(
                "GitHub username", key="username_input", max_chars=100,
                placeholder="octocat",
                help="Type a username (or paste a github.com profile link) and press Enter.",
            )
            st.checkbox("Include forks in language analysis", key="opt_include_forks",
                        help="Forks mostly contain other people's code, so they are skipped by default.")
            st.slider(
                "Repositories to analyze for languages", 5, 50, 20, key="opt_max_repos",
                help="Each repository needs one API request. Without a token GitHub allows "
                     "60 requests per hour, so keep this small.",
            )
            submitted = st.form_submit_button("Analyze profile", type="primary", width="stretch")

        if submitted:
            username = clean_username(st.session_state.get("username_input", ""))
            valid, message = validate_username(username)
            if not valid:
                st.error(message)
            else:
                st.session_state["pending"] = {
                    "username": username,
                    "include_forks": bool(st.session_state.get("opt_include_forks", False)),
                    "max_repos": int(st.session_state.get("opt_max_repos", 20)),
                }


def render_sidebar_footer() -> None:
    token = get_token()
    with st.sidebar:
        if st.session_state.get("analysis") or st.session_state.get("error"):
            st.button("Refresh data", on_click=request_refresh, width="stretch",
                      help="Clear cached results and fetch everything again.")
            st.button("Start new search", on_click=request_new_search, width="stretch")
        st.divider()
        st.markdown("**API status**")
        st.caption("Using a GitHub token." if token else
                   "No token configured: GitHub allows 60 requests per hour.")
        limit = cached_rate_limit(token)
        if limit:
            st.caption(f"Requests left: {limit['remaining']} of {limit['limit']}. "
                       f"Resets at {epoch_to_text(limit['reset'])}.")
            if limit["remaining"] <= 10:
                st.warning("Few API requests remain. Results may be incomplete until the limit resets.")
        else:
            st.caption("Rate limit status is not available right now.")


# --------------------------------------------------------------------------- #
# Welcome and error screens
# --------------------------------------------------------------------------- #
def render_welcome() -> None:
    st.title("GitHub Profile Analyzer")
    st.write("Enter a public GitHub username in the sidebar to see a profile summary, "
             "repository statistics, programming languages and recent public activity.")
    st.markdown("Try an example:")
    columns = st.columns(len(EXAMPLE_USERNAMES) + 2)
    for column, name in zip(columns, EXAMPLE_USERNAMES):
        column.button(name, key=f"example_{name}", on_click=request_example, args=(name,),
                      width="stretch")
    st.caption("Data comes from the official GitHub REST API. Only public information is shown.")


def render_error(error: GitHubAPIError) -> None:
    st.title("GitHub Profile Analyzer")
    if error.kind == NOT_FOUND:
        st.error(f"**User not found.** {error.message}")
        st.caption("Usernames contain only letters, numbers and hyphens. "
                   "You can also paste a full profile link.")
    elif error.kind == RATE_LIMIT:
        st.warning(f"**Rate limit reached.** {error.message}")
    else:
        st.error(f"**Could not load the profile.** {error.message}")
    st.caption("Use Refresh data in the sidebar to try again.")


# --------------------------------------------------------------------------- #
# Overview tab
# --------------------------------------------------------------------------- #
def render_profile(profile: dict) -> None:
    login = str(profile.get("login", ""))
    profile_url = (github_url(profile.get("html_url"))
                   or f"https://github.com/{quote(login, safe='')}")
    avatar = avatar_url(profile.get("avatar_url"))
    name = profile.get("name") or login
    website = normalize_url(profile.get("blog"))

    avatar_col, info_col = st.columns([1, 4], vertical_alignment="center")
    with avatar_col:
        if avatar:
            st.markdown(
                f'<a href="{safe_text(profile_url)}" target="_blank" rel="noopener noreferrer" '
                f'title="Open GitHub profile"><img class="avatar" src="{safe_text(avatar)}" '
                f'alt="Profile picture of {safe_text(login)}"></a>',
                unsafe_allow_html=True,
            )
    with info_col:
        parts = [
            f'<div class="profile-name">{safe_text(name, 120)}</div>',
            f'<div class="profile-login"><a href="{safe_text(profile_url)}" target="_blank" '
            f'rel="noopener noreferrer">@{safe_text(login)}</a></div>',
        ]
        bio = safe_text(profile.get("bio"), 400)
        if bio:
            parts.append(f'<div class="profile-bio">{bio}</div>')
        website_html = (
            f'<a href="{safe_text(website)}" target="_blank" rel="noopener noreferrer">'
            f'{safe_text(re.sub(r"^https?://", "", website), 60)}</a>'
            if website else f'<span class="muted">{NOT_PROVIDED}</span>'
        )
        facts = [
            fact_html("Location", text_or_not_provided(profile.get("location"))),
            fact_html("Company", text_or_not_provided(profile.get("company"))),
            fact_html("Website", website_html),
            fact_html("Joined GitHub", safe_text(format_date(profile.get("created_at")))),
            fact_html("Account type", safe_text(profile.get("type") or "User")),
        ]
        parts.append(f'<div class="facts">{"".join(facts)}</div>')
        st.markdown("".join(parts), unsafe_allow_html=True)
        st.link_button("View on GitHub", profile_url)

    if profile.get("type") == "Organization":
        st.info("This is an organization account. Followers and repositories are shown as GitHub reports them.")


def render_stats(profile: dict, df: pd.DataFrame, limit_reached: bool) -> None:
    totals = compute_totals(df)
    reported = profile.get("public_repos")
    st.subheader("Statistics")
    row1 = st.columns(3)
    row2 = st.columns(3)
    row1[0].metric("Public repositories", format_number(reported if reported is not None else totals["repo_count"]))
    row1[1].metric("Followers", format_number(profile.get("followers")))
    row1[2].metric("Following", format_number(profile.get("following")))
    row2[0].metric("Stars received", format_number(totals["stars"]),
                   help="Sum of stars on the public repositories that were fetched.")
    row2[1].metric("Forks of these repositories", format_number(totals["forks"]),
                   help="Sum of the fork counts of the fetched repositories.")
    row2[2].metric("Total repository size", format_size_kb(totals["size_kb"]),
                   help="Sum of the repository sizes GitHub reports (in KB). This is storage size, not lines of code.")

    fetched = totals["repo_count"]
    if limit_reached:
        st.warning(
            f"Only the first {fetched} repositories were fetched (the limit is "
            f"{MAX_REPO_PAGES * 100}). Stars, forks and size cover those repositories only."
        )
    elif reported is not None and fetched != reported:
        st.caption(f"Stars, forks and size are calculated from the {fetched} repositories retrieved "
                   f"(GitHub's profile counter says {format_number(reported)}).")
    else:
        st.caption(f"Stars, forks and size are calculated from all {fetched} public repositories retrieved.")


# --------------------------------------------------------------------------- #
# Repositories tab
# --------------------------------------------------------------------------- #
def render_repositories(df: pd.DataFrame) -> None:
    st.subheader("Repository explorer")
    if df.empty:
        st.info("This user has no public repositories.")
        return

    f1, f2, f3, f4 = st.columns([2.2, 1.4, 1.2, 1.4])
    query = f1.text_input("Search by repository name", key="repo_query", placeholder="Type part of a name")
    language = f2.selectbox("Language", ["All"] + language_options(df), key="repo_language")
    repo_type = f3.selectbox("Type", REPO_TYPE_OPTIONS, key="repo_type")
    sort_by = f4.selectbox("Sort by", list(SORT_OPTIONS), key="repo_sort")

    g1, g2, g3, _ = st.columns([1.2, 1.2, 1.2, 2])
    reverse = g1.checkbox("Reverse order", key="repo_reverse")
    page_size = g2.selectbox("Rows per page", [10, 25, 50, 100], index=1, key="repo_page_size")

    result = sort_repos(filter_repos(df, query, language, repo_type), sort_by, reverse)
    total_pages = max(1, math.ceil(len(result) / page_size))
    page = g3.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1,
                           disabled=total_pages == 1)

    if result.empty:
        st.info("No repositories match these filters. Try a different search or choose 'All'.")
        return

    page = min(int(page), total_pages)
    start = (page - 1) * page_size
    chunk = result.iloc[start:start + page_size]

    view = pd.DataFrame({
        "name": chunk["name"],
        "description": chunk["description"].replace("", "No description"),
        "language": chunk["language"],
        "stars": chunk["stars"],
        "forks": chunk["forks"],
        "open_issues": chunk["open_issues"],
        "updated_at": chunk["updated_at"],
        "visibility": chunk["visibility"],
        "type": chunk["is_fork"].map({True: "Fork", False: "Original"}),
        "url": chunk["url"],
    })
    st.dataframe(
        view, hide_index=True, width="stretch",
        column_config={
            "name": st.column_config.TextColumn("Repository", width="medium"),
            "description": st.column_config.TextColumn("Description", width="large"),
            "language": st.column_config.TextColumn("Language"),
            "stars": st.column_config.NumberColumn("Stars", format="%d"),
            "forks": st.column_config.NumberColumn("Forks", format="%d"),
            "open_issues": st.column_config.NumberColumn("Open issues", format="%d"),
            "updated_at": st.column_config.DatetimeColumn("Last updated", format="DD MMM YYYY"),
            "visibility": st.column_config.TextColumn("Visibility"),
            "type": st.column_config.TextColumn("Type"),
            "url": st.column_config.LinkColumn("Repository URL", display_text="Open on GitHub"),
        },
    )
    st.caption(f"Showing {start + 1}-{start + len(chunk)} of {len(result)} matching repositories "
               f"(page {page} of {total_pages}; {len(df)} repositories fetched in total).")


# --------------------------------------------------------------------------- #
# Languages tab
# --------------------------------------------------------------------------- #
def render_languages(analysis: dict) -> None:
    st.subheader("Programming languages")
    result: LanguageFetchResult = analysis["language_result"]
    analyzed = len(result.languages)

    st.caption(
        "Language shares are based on the bytes of code GitHub detects in each repository "
        "(repository languages endpoint). They do not measure time spent coding, effort or skill."
    )
    if analysis["eligible_repos"] == 0:
        st.info("There are no repositories to analyze. Try enabling 'Include forks' and searching again.")
        return

    scope = "repositories" if analysis["include_forks"] else "original (non-fork) repositories"
    st.markdown(
        f"Language data was loaded for **{analyzed}** of **{analysis['eligible_repos']}** {scope}, "
        f"starting with the most recently pushed. You can change this in the sidebar."
    )
    if result.rate_limited:
        st.warning(f"GitHub's rate limit was reached, so {result.skipped} repositories were not analyzed. "
                   "Refresh after the limit resets, or configure a token (see the README).")
    if result.failed:
        shown = ", ".join(md_escape(name) for name in result.failed[:10])
        more = f" and {len(result.failed) - 10} more" if len(result.failed) > 10 else ""
        st.warning(f"Language data could not be loaded for {len(result.failed)} repositories: {shown}{more}.")

    lang_df = aggregate_languages(result.languages.values())
    if lang_df.empty:
        st.info("No programming languages were detected in the analyzed repositories.")
        return

    without = count_repos_without_languages(result.languages)
    if without:
        st.caption(f"{without} analyzed repositories had no detectable language (for example, empty or "
                   "documentation-only repositories).")

    left, right = st.columns(2)
    with left:
        grouped = group_small_languages(lang_df, 8)
        donut = px.pie(grouped, names="language", values="bytes", hole=0.55, color="language",
                       color_discrete_sequence=PALETTE, color_discrete_map={OTHER_LABEL: "#94A3B8"},
                       title="Language distribution")
        donut.update_traces(sort=False, textinfo="percent", textposition="inside",
                            hovertemplate="%{label}<br>%{value:,} bytes<br>%{percent}<extra></extra>",
                            marker=dict(line=dict(color="#FFFFFF", width=2)))
        show_chart(style_figure(donut), "chart_language_donut")
    with right:
        top = lang_df.head(10).sort_values("percentage")
        bar = px.bar(top, x="percentage", y="language", orientation="h", custom_data=["bytes"],
                     text=top["percentage"].map(lambda value: f"{value:.1f}%"),
                     color_discrete_sequence=[ACCENT], title="Most-used languages")
        bar.update_traces(textposition="outside", cliponaxis=False,
                          hovertemplate="%{y}<br>%{x:.1f}%<br>%{customdata[0]:,} bytes<extra></extra>")
        bar.update_xaxes(title="Share of detected code", ticksuffix="%")
        bar.update_yaxes(title=None)
        show_chart(style_figure(bar), "chart_language_bar")

    table = pd.DataFrame({
        "Language": lang_df["language"],
        "Size": lang_df["bytes"].map(format_bytes),
        "Bytes": lang_df["bytes"],
        "Share": lang_df["percentage"],
        "Repositories": lang_df["repos"],
    })
    st.dataframe(
        table, hide_index=True, width="stretch",
        column_config={
            "Bytes": st.column_config.NumberColumn("Bytes", format="localized"),
            "Share": st.column_config.ProgressColumn("Share", format="%.1f%%", min_value=0, max_value=100),
        },
    )


# --------------------------------------------------------------------------- #
# Insights tab
# --------------------------------------------------------------------------- #
def highlight_card(title: str, item: Optional[dict], empty_message: str, stat_label: str, stat_key: str) -> None:
    with st.container(border=True):
        st.caption(title)
        if not item:
            st.write(empty_message)
            return
        st.markdown(f"### {md_escape(item['name'])}")
        st.write(f"{format_number(item[stat_key])} {stat_label}")
        url = github_url(item.get("url"))
        if url:
            st.link_button("Open repository", url, key=f"open_{title}")


def link_table(items: list[dict], date_column: bool = True) -> pd.DataFrame:
    rows = []
    for item in items:
        row = {"Repository": item["name"]}
        if date_column:
            row["Last updated"] = f"{format_date(item['updated_at'])} ({relative_time(item['updated_at'])})"
        row["Link"] = item["url"]
        rows.append(row)
    return pd.DataFrame(rows)


def render_insights(df: pd.DataFrame) -> None:
    st.subheader("Repository insights")
    if df.empty:
        st.info("There are no repositories to analyze.")
        return
    insights = repo_insights(df)

    c1, c2 = st.columns(2)
    with c1:
        highlight_card("Most-starred repository", insights["most_starred"],
                       "None of the fetched repositories has a star yet.", "stars", "stars")
    with c2:
        highlight_card("Most-forked repository", insights["most_forked"],
                       "None of the fetched repositories has been forked yet.", "forks", "forks")

    m1, m2, m3 = st.columns(3)
    m1.metric("Original repositories", format_number(insights["original_count"]))
    m2.metric("Forked repositories", format_number(insights["fork_count"]))
    m3.metric("Archived repositories", format_number(insights["archived_count"]))

    link_config = {"Link": st.column_config.LinkColumn("Link", display_text="Open on GitHub")}
    st.markdown("#### Recently updated")
    st.dataframe(link_table(insights["recently_updated"]), hide_index=True, width="stretch",
                 column_config=link_config)

    st.markdown("#### Repositories without a description")
    missing = insights["without_description"]
    if not missing:
        st.success("Every fetched repository has a description.")
    else:
        st.caption(f"{len(missing)} of {len(df)} fetched repositories have no description.")
        st.dataframe(link_table(missing[:25], date_column=False), hide_index=True, width="stretch",
                     column_config=link_config)
        if len(missing) > 25:
            st.caption(f"Showing 25 of {len(missing)}.")

    st.markdown("#### Repositories grouped by primary language")
    by_language = insights["by_language"]
    st.caption("Uses the main language GitHub shows for each repository. "
               "Repositories without one are listed as 'Not specified'.")
    chart_data = by_language.head(12).sort_values("repos")
    bar = px.bar(chart_data, x="repos", y="language", orientation="h",
                 color_discrete_sequence=[PALETTE[1]], title="Repositories per primary language")
    bar.update_xaxes(title="Repositories", dtick=1 if chart_data["repos"].max() <= 10 else None)
    bar.update_yaxes(title=None)
    show_chart(style_figure(bar, 340), "chart_repos_by_language")
    st.dataframe(
        by_language.rename(columns={"language": "Language", "repos": "Repositories",
                                    "stars": "Stars", "forks": "Forks"}),
        hide_index=True, width="stretch",
    )


# --------------------------------------------------------------------------- #
# Activity tab
# --------------------------------------------------------------------------- #
def render_activity(analysis: dict) -> None:
    st.subheader("Recent public activity")
    st.caption(
        "This uses GitHub's Events API, which only returns recent public events (GitHub keeps at most "
        "300 events from the last 90 days; this app loads the latest 100). It is not a complete "
        "contribution history, it excludes private activity, and it cannot give exact contribution counts."
    )
    if analysis["events_error"]:
        st.warning(f"Recent events could not be loaded. {analysis['events_error']}")
        return

    events_df = summarize_events(analysis["events"], limit=100)
    if events_df.empty:
        st.info("No recent public events were found for this user.")
        return

    newest = events_df["created_at"].max()
    oldest = events_df["created_at"].min()
    st.markdown(f"Loaded **{len(events_df)}** recent public events, from "
                f"{format_date(oldest)} to {format_date(newest)}.")

    counts = event_type_counts(events_df)
    chart = px.bar(counts, x="type", y="count", color_discrete_sequence=[ACCENT],
                   title="Events by type")
    chart.update_xaxes(title=None)
    chart.update_yaxes(title="Events", dtick=1 if counts["count"].max() <= 10 else None)
    show_chart(style_figure(chart, 320), "chart_event_types")

    shown = st.selectbox("Events to show", [10, 25, 50, 100], index=1, key="events_shown")
    table = pd.DataFrame({
        "When": events_df["created_at"].map(format_datetime),
        "Ago": events_df["created_at"].map(relative_time),
        "Type": events_df["type"],
        "Repository": events_df["repository"],
        "Details": events_df["details"],
    }).head(shown)
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Details": st.column_config.TextColumn("Details", width="large")})


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def render_dashboard(analysis: dict) -> None:
    profile = analysis["profile"]
    df = analysis["repos_df"]
    login = str(profile.get("login", ""))

    st.title("GitHub Profile Analyzer")
    st.success(f"Loaded @{md_escape(login)}: {len(df)} public repositories fetched at "
               f"{format_datetime(analysis['fetched_at'])}.")

    overview, repositories, languages, insights, activity = st.tabs(
        ["Overview", "Repositories", "Languages", "Insights", "Activity"])
    with overview:
        render_profile(profile)
        st.divider()
        render_stats(profile, df, analysis["limit_reached"])
    with repositories:
        render_repositories(df)
    with languages:
        render_languages(analysis)
    with insights:
        render_insights(df)
    with activity:
        render_activity(analysis)

    st.caption("Data comes from the official GitHub REST API and is cached for a few minutes. "
               "This project is not affiliated with GitHub.")


def main() -> None:
    st.set_page_config(page_title="GitHub Profile Analyzer", page_icon="📊", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()

    render_search_form()
    pending = st.session_state.get("pending")
    if pending:
        st.session_state["pending"] = None
        run_analysis(**pending)
    render_sidebar_footer()

    if st.session_state["error"] is not None:
        render_error(st.session_state["error"])
    elif st.session_state["analysis"] is not None:
        render_dashboard(st.session_state["analysis"])
    else:
        render_welcome()


main()
