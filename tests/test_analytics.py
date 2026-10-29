"""Tests for src/analytics.py. No internet access is needed."""

import pandas as pd
import pytest

from src import analytics as an


def make_repo(name, stars=0, forks=0, language="Python", description="A repo",
              fork=False, updated="2024-01-01T00:00:00Z", pushed=None, size=10, **extra):
    repo = {
        "name": name,
        "description": description,
        "language": language,
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": 1,
        "size": size,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": updated,
        "pushed_at": pushed or updated,
        "visibility": "public",
        "fork": fork,
        "archived": False,
        "html_url": f"https://github.com/someone/{name}",
    }
    repo.update(extra)
    return repo


@pytest.fixture
def repos():
    return [
        make_repo("alpha", stars=5, forks=2, language="Python", updated="2024-03-01T00:00:00Z"),
        make_repo("Beta", stars=20, forks=0, language="JavaScript", description="",
                  updated="2024-01-15T00:00:00Z"),
        make_repo("gamma", stars=1, forks=9, language=None, description=None, fork=True,
                  updated="2023-06-01T00:00:00Z"),
        make_repo("delta", stars=5, forks=1, language="Python", updated="2024-02-01T00:00:00Z"),
    ]


@pytest.fixture
def df(repos):
    return an.repos_to_dataframe(repos)


# ------------------------------- DataFrame ---------------------------------- #
def test_dataframe_has_expected_columns(df):
    assert list(df.columns) == an.REPO_COLUMNS
    assert len(df) == 4


def test_missing_description_and_language_are_handled(df):
    gamma = df[df["name"] == "gamma"].iloc[0]
    assert gamma["description"] == ""
    assert gamma["language"] == an.NO_LANGUAGE


def test_invalid_entries_are_skipped():
    df = an.repos_to_dataframe([None, "oops", {"description": "no name"}, make_repo("ok")])
    assert list(df["name"]) == ["ok"]


def test_missing_or_invalid_numbers_become_zero():
    df = an.repos_to_dataframe([{"name": "x", "stargazers_count": None, "forks_count": "abc",
                                 "size": -5}])
    row = df.iloc[0]
    assert (row["stars"], row["forks"], row["size_kb"]) == (0, 0, 0)


def test_empty_repository_list():
    df = an.repos_to_dataframe([])
    assert df.empty
    assert list(df.columns) == an.REPO_COLUMNS
    totals = an.compute_totals(df)
    assert totals["repo_count"] == 0 and totals["stars"] == 0 and totals["size_kb"] == 0
    assert an.repos_to_dataframe(None).empty


# --------------------------------- Totals ----------------------------------- #
def test_star_fork_and_size_totals(df):
    totals = an.compute_totals(df)
    assert totals["repo_count"] == 4
    assert totals["stars"] == 31
    assert totals["forks"] == 12
    assert totals["size_kb"] == 40
    assert totals["fork_repos"] == 1
    assert totals["original_repos"] == 3


# ------------------------------ Filter and sort ------------------------------ #
def test_sort_by_stars_descending(df):
    result = an.sort_repos(df, "Stars")
    assert list(result["name"])[0] == "Beta"
    assert list(result["stars"]) == sorted(result["stars"], reverse=True)


def test_sort_by_forks_descending(df):
    assert list(an.sort_repos(df, "Forks")["name"])[0] == "gamma"


def test_sort_by_name_is_case_insensitive_a_to_z(df):
    assert list(an.sort_repos(df, "Name")["name"]) == ["alpha", "Beta", "delta", "gamma"]


def test_sort_by_last_updated_newest_first(df):
    assert list(an.sort_repos(df, "Last updated")["name"]) == ["alpha", "delta", "Beta", "gamma"]


def test_reverse_flips_the_order(df):
    assert list(an.sort_repos(df, "Name", reverse=True)["name"]) == ["gamma", "delta", "Beta", "alpha"]


def test_sort_puts_missing_dates_last():
    df = an.repos_to_dataframe([
        make_repo("no_date", updated=None),
        make_repo("old", updated="2020-01-01T00:00:00Z"),
        make_repo("new", updated="2024-01-01T00:00:00Z"),
    ])
    assert list(an.sort_repos(df, "Last updated")["name"]) == ["new", "old", "no_date"]
    assert list(an.sort_repos(df, "Last updated", reverse=True)["name"])[-1] == "no_date"


def test_unknown_sort_option_raises(df):
    with pytest.raises(ValueError):
        an.sort_repos(df, "Colour")


def test_sorting_an_empty_frame_does_not_fail():
    assert an.sort_repos(an.repos_to_dataframe([]), "Stars").empty


def test_filter_by_name_language_and_type(df):
    assert list(an.filter_repos(df, name_query="ALP")["name"]) == ["alpha"]
    assert set(an.filter_repos(df, language="Python")["name"]) == {"alpha", "delta"}
    assert list(an.filter_repos(df, repo_type="Forks")["name"]) == ["gamma"]
    assert len(an.filter_repos(df, repo_type="Original")) == 3
    assert len(an.filter_repos(df, language="All")) == 4
    assert an.filter_repos(df, name_query="does-not-exist").empty


def test_name_search_treats_special_characters_literally(df):
    assert an.filter_repos(df, name_query=".*").empty


def test_language_options(df):
    assert an.language_options(df) == ["JavaScript", "Not specified", "Python"]
    assert an.language_options(an.repos_to_dataframe([])) == []


def test_select_repos_for_language_analysis(df):
    names = an.select_repos_for_language_analysis(df, limit=2)
    assert names == ["alpha", "delta"]  # most recently pushed originals
    assert "gamma" not in an.select_repos_for_language_analysis(df, limit=10)
    assert "gamma" in an.select_repos_for_language_analysis(df, limit=10, include_forks=True)
    assert an.select_repos_for_language_analysis(df, limit=0) == []
    assert an.select_repos_for_language_analysis(an.repos_to_dataframe([]), limit=5) == []


# ------------------------------- Languages ----------------------------------- #
def test_language_percentages():
    result = an.aggregate_languages([{"Python": 750, "HTML": 250}, {"Python": 250}])
    assert list(result["language"]) == ["Python", "HTML"]
    assert list(result["bytes"]) == [1000, 250]
    assert result["percentage"].iloc[0] == pytest.approx(80.0)
    assert result["percentage"].iloc[1] == pytest.approx(20.0)
    assert result["percentage"].sum() == pytest.approx(100.0)
    assert list(result["repos"]) == [2, 1]


def test_empty_language_data():
    for empty in ([], [{}], [{}, {}], [None], [{"Python": 0}]):
        result = an.aggregate_languages(empty)
        assert result.empty
        assert list(result.columns) == an.LANGUAGE_COLUMNS


def test_invalid_language_values_are_ignored():
    result = an.aggregate_languages([{"Python": 100, "Bad": "x", "Neg": -5, "Flag": True, "": 10}, "nope"])
    assert list(result["language"]) == ["Python"]
    assert result["percentage"].iloc[0] == pytest.approx(100.0)


def test_group_small_languages_creates_other_row():
    maps = [{f"Lang{i}": 100 - i for i in range(12)}]
    full = an.aggregate_languages(maps)
    grouped = an.group_small_languages(full, top_n=5)
    assert len(grouped) == 6
    assert grouped["language"].iloc[-1] == an.OTHER_LABEL
    assert grouped["bytes"].sum() == full["bytes"].sum()
    assert grouped["percentage"].sum() == pytest.approx(100.0)
    assert len(an.group_small_languages(full, top_n=20)) == 12


def test_count_repos_without_languages():
    maps = {"a": {"Python": 10}, "b": {}, "c": None}
    assert an.count_repos_without_languages(maps) == 2


# -------------------------------- Insights ----------------------------------- #
def test_insights_most_starred_and_forked(df):
    insights = an.repo_insights(df)
    assert insights["most_starred"]["name"] == "Beta"
    assert insights["most_starred"]["stars"] == 20
    assert insights["most_forked"]["name"] == "gamma"
    assert insights["original_count"] == 3
    assert insights["fork_count"] == 1


def test_insights_repositories_without_descriptions(df):
    names = {item["name"] for item in an.repo_insights(df)["without_description"]}
    assert names == {"Beta", "gamma"}


def test_insights_recently_updated_order_and_limit(df):
    recent = an.repo_insights(df, recent_count=2)["recently_updated"]
    assert [item["name"] for item in recent] == ["alpha", "delta"]


def test_insights_do_not_name_a_winner_when_everything_is_zero():
    df = an.repos_to_dataframe([make_repo("a"), make_repo("b")])
    insights = an.repo_insights(df)
    assert insights["most_starred"] is None
    assert insights["most_forked"] is None


def test_insights_for_empty_data():
    insights = an.repo_insights(an.repos_to_dataframe([]))
    assert insights["most_starred"] is None
    assert insights["recently_updated"] == []
    assert insights["without_description"] == []
    assert insights["by_language"].empty


def test_language_summary_groups_repositories(df):
    summary = an.language_summary(df)
    python = summary[summary["language"] == "Python"].iloc[0]
    assert (python["repos"], python["stars"], python["forks"]) == (2, 10, 3)
    assert summary["language"].iloc[0] == "Python"  # most repositories first


# --------------------------------- Events ------------------------------------ #
def make_event(event_type, payload=None, repo="someone/project", created="2024-05-01T10:00:00Z"):
    return {"type": event_type, "repo": {"name": repo}, "created_at": created, "payload": payload or {}}


def test_push_event_description():
    event = make_event("PushEvent", {"ref": "refs/heads/main", "size": 2,
                                     "commits": [{"message": "first"}, {"message": "Fix bug\n\nlong body"}]})
    text = an.describe_event(event)
    assert "2 commits" in text and "main" in text and "Fix bug" in text and "long body" not in text


def test_event_descriptions_for_common_types():
    assert an.describe_event(make_event("WatchEvent")) == "Starred the repository"
    created = an.describe_event(make_event("CreateEvent", {"ref_type": "branch", "ref": "dev"}))
    assert created == "Created branch 'dev'"
    pr = make_event("PullRequestEvent", {"action": "closed", "number": 7,
                                         "pull_request": {"title": "Add tests", "merged": True}})
    assert an.describe_event(pr) == "Merged pull request #7: Add tests"
    issue = make_event("IssuesEvent", {"action": "opened", "issue": {"number": 3, "title": "Bug"}})
    assert an.describe_event(issue) == "Opened issue #3: Bug"


def test_unknown_or_malformed_events_do_not_crash():
    assert an.describe_event({"type": "SomethingNewEvent"}) == "No further details available"
    assert an.describe_event({"type": "PushEvent", "payload": "bad"}).startswith("Pushed")
    df = an.summarize_events([None, "x", {"type": "WatchEvent"}])
    assert len(df) == 1
    assert df["repository"].iloc[0] == "Unknown"


def test_summarize_events_limit_and_columns():
    events = [make_event("WatchEvent", created=f"2024-05-0{i}T10:00:00Z") for i in range(1, 6)]
    df = an.summarize_events(events, limit=3)
    assert list(df.columns) == an.EVENT_COLUMNS
    assert len(df) == 3
    assert df["type"].iloc[0] == "Star"


def test_summarize_events_empty():
    assert an.summarize_events([]).empty
    assert an.summarize_events(None).empty
    assert an.event_type_counts(an.summarize_events([])).empty


def test_event_type_counts():
    events = [make_event("WatchEvent"), make_event("WatchEvent"), make_event("PushEvent")]
    counts = an.event_type_counts(an.summarize_events(events))
    assert dict(zip(counts["type"], counts["count"])) == {"Star": 2, "Push": 1}


def test_no_network_is_needed():
    # The module only works on data passed in - make sure it does not import requests.
    assert "requests" not in vars(an)
    assert isinstance(an.repos_to_dataframe([]), pd.DataFrame)
