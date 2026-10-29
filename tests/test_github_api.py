"""Tests for src/github_api.py using a fake session - no internet access needed."""

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from src import github_api as api


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad_json = bad_json
        self.headers = CaseInsensitiveDict(headers or {})

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) and records the calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ------------------------------- Users --------------------------------------- #
def test_fetch_user_success_and_request_details():
    session = FakeSession(FakeResponse(200, {"login": "octocat", "public_repos": 8}))
    user = api.fetch_user("octocat", session=session)
    assert user["login"] == "octocat"
    call = session.calls[0]
    assert call["url"] == "https://api.github.com/users/octocat"
    assert call["timeout"] == api.REQUEST_TIMEOUT
    assert "Authorization" not in call["headers"]  # no token by default


def test_token_is_sent_only_when_given():
    session = FakeSession(FakeResponse(200, {"login": "x"}))
    api.fetch_user("x", token="secret-token", session=session)
    assert session.calls[0]["headers"]["Authorization"] == "Bearer secret-token"


def test_user_not_found():
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("ghost-user", session=FakeSession(FakeResponse(404, {"message": "Not Found"})))
    assert info.value.kind == api.NOT_FOUND
    assert "ghost-user" in str(info.value)


def test_unexpected_profile_data_is_rejected():
    for body in ([], {"no": "login"}, "text"):
        with pytest.raises(api.GitHubAPIError) as info:
            api.fetch_user("x", session=FakeSession(FakeResponse(200, body)))
        assert info.value.kind == api.INVALID


# ------------------------------ Error handling ------------------------------- #
def test_rate_limit_403_with_remaining_zero():
    response = FakeResponse(403, {"message": "API rate limit exceeded"},
                            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"})
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(response))
    assert info.value.kind == api.RATE_LIMIT
    assert info.value.reset_at == 4102444800
    assert "rate limit" in str(info.value).lower()


def test_rate_limit_429_and_message_based_detection():
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(FakeResponse(429, {}, {"Retry-After": "30"})))
    assert info.value.kind == api.RATE_LIMIT
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(FakeResponse(403, {"message": "You have exceeded a secondary rate limit"})))
    assert info.value.kind == api.RATE_LIMIT


def test_plain_403_is_forbidden_not_rate_limit():
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(FakeResponse(403, {"message": "Forbidden"})))
    assert info.value.kind == api.FORBIDDEN


@pytest.mark.parametrize("status, kind", [(401, api.AUTH), (500, api.SERVER), (503, api.SERVER), (418, api.HTTP)])
def test_other_status_codes(status, kind):
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(FakeResponse(status, {})))
    assert info.value.kind == kind


def test_network_errors():
    for error in (requests.exceptions.Timeout(), requests.exceptions.ConnectionError()):
        with pytest.raises(api.GitHubAPIError) as info:
            api.fetch_user("x", session=FakeSession(error))
        assert info.value.kind == api.NETWORK


def test_invalid_json_is_reported():
    with pytest.raises(api.GitHubAPIError) as info:
        api.fetch_user("x", session=FakeSession(FakeResponse(200, bad_json=True)))
    assert info.value.kind == api.INVALID


# ------------------------------- Repositories -------------------------------- #
def repo_page(count, start=0):
    return [{"name": f"repo{start + i}"} for i in range(count)]


def test_repos_single_page():
    session = FakeSession(FakeResponse(200, repo_page(3)))
    repos, limit_reached = api.fetch_repos("x", session=session)
    assert len(repos) == 3 and limit_reached is False
    assert session.calls[0]["params"]["per_page"] == 100


def test_repos_pagination_continues_until_short_page():
    session = FakeSession(FakeResponse(200, repo_page(100)), FakeResponse(200, repo_page(5, 100)))
    repos, limit_reached = api.fetch_repos("x", session=session)
    assert len(repos) == 105 and limit_reached is False
    assert [c["params"]["page"] for c in session.calls] == [1, 2]


def test_repos_stop_at_max_pages_and_report_limit():
    session = FakeSession(*[FakeResponse(200, repo_page(100, i * 100)) for i in range(3)])
    repos, limit_reached = api.fetch_repos("x", session=session, max_pages=3)
    assert len(repos) == 300 and limit_reached is True
    assert len(session.calls) == 3


def test_repos_with_no_items_and_bad_items():
    assert api.fetch_repos("x", session=FakeSession(FakeResponse(200, []))) == ([], False)
    repos, _ = api.fetch_repos("x", session=FakeSession(FakeResponse(200, [None, 5, {"name": "ok"}, {"id": 1}])))
    assert [r["name"] for r in repos] == ["ok"]
    with pytest.raises(api.GitHubAPIError):
        api.fetch_repos("x", session=FakeSession(FakeResponse(200, {"message": "not a list"})))


# ------------------------------- Languages / events -------------------------- #
def test_fetch_repo_languages_cleans_values():
    body = {"Python": 100, "Bad": "x", "Zero": 0, "Flag": True}
    session = FakeSession(FakeResponse(200, body))
    assert api.fetch_repo_languages("o", "r", session=session) == {"Python": 100}
    assert session.calls[0]["url"].endswith("/repos/o/r/languages")
    with pytest.raises(api.GitHubAPIError):
        api.fetch_repo_languages("o", "r", session=FakeSession(FakeResponse(200, [])))


def test_fetch_events():
    session = FakeSession(FakeResponse(200, [{"type": "PushEvent"}, "junk"]))
    assert api.fetch_events("x", session=session) == [{"type": "PushEvent"}]
    assert session.calls[0]["url"].endswith("/users/x/events/public")
    with pytest.raises(api.GitHubAPIError):
        api.fetch_events("x", session=FakeSession(FakeResponse(200, {})))


def test_fetch_rate_limit():
    body = {"resources": {"core": {"limit": 60, "remaining": 41, "reset": 1700000000}}}
    assert api.fetch_rate_limit(session=FakeSession(FakeResponse(200, body))) == {
        "limit": 60, "remaining": 41, "reset": 1700000000}
    assert api.fetch_rate_limit(session=FakeSession(FakeResponse(200, {}))) is None
    assert api.fetch_rate_limit(session=FakeSession(requests.exceptions.ConnectionError())) is None


def test_fetch_languages_for_repos_collects_results_and_failures():
    def fetch_one(owner, repo):
        if repo == "broken":
            raise api.GitHubAPIError(api.SERVER, "boom")
        return {"Python": 10}

    progress_calls = []
    result = api.fetch_languages_for_repos(
        "me", ["a", "broken", "b"], fetch_one, progress=lambda done, total: progress_calls.append((done, total)))
    assert set(result.languages) == {"a", "b"}
    assert result.failed == ["broken"]
    assert result.rate_limited is False and result.skipped == 0
    assert progress_calls[-1] == (3, 3)


def test_fetch_languages_stops_at_rate_limit():
    def fetch_one(owner, repo):
        if repo == "c":
            raise api.GitHubAPIError(api.RATE_LIMIT, "limit")
        return {"Go": 5}

    result = api.fetch_languages_for_repos("me", ["a", "b", "c", "d", "e"], fetch_one)
    assert set(result.languages) == {"a", "b"}
    assert result.rate_limited is True
    assert result.skipped == 3


def test_get_token_reads_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "  abc123  ")
    assert api.get_token() == "abc123"
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr(api, "ENV_FILE", api.ENV_FILE.parent / "definitely-missing.env")
    assert api.get_token() is None
