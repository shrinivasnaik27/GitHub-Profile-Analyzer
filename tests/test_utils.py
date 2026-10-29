"""Tests for src/utils.py (dates, numbers, validation and safe text)."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from src import utils


# ------------------------------- Dates --------------------------------------- #
def test_format_date_valid():
    assert utils.format_date("2011-01-25T18:44:36Z") == "25 Jan 2011"
    assert utils.format_date("2011-01-25T18:44:36+00:00") == "25 Jan 2011"
    assert utils.format_date(datetime(2020, 3, 5, tzinfo=timezone.utc)) == "05 Mar 2020"


@pytest.mark.parametrize("bad", [None, "", "   ", "not a date", 12345, [], pd.NaT])
def test_format_date_invalid_values(bad):
    assert utils.format_date(bad) == utils.UNKNOWN_DATE
    assert utils.format_datetime(bad) == utils.UNKNOWN_DATE
    assert utils.relative_time(bad) == utils.UNKNOWN_DATE


def test_format_datetime_is_in_utc():
    assert utils.format_datetime("2024-05-01T10:30:00Z") == "01 May 2024, 10:30 UTC"
    assert utils.format_datetime("2024-05-01T12:30:00+02:00") == "01 May 2024, 10:30 UTC"


def test_relative_time():
    now = datetime(2024, 5, 10, 12, 0, tzinfo=timezone.utc)
    assert utils.relative_time("2024-05-10T11:59:50Z", now) == "just now"
    assert utils.relative_time("2024-05-10T11:30:00Z", now) == "30 minutes ago"
    assert utils.relative_time("2024-05-10T11:00:00Z", now) == "1 hour ago"
    assert utils.relative_time("2024-05-07T12:00:00Z", now) == "3 days ago"
    assert utils.relative_time("2024-03-10T12:00:00Z", now) == "2 months ago"
    assert utils.relative_time("2021-05-10T12:00:00Z", now) == "3 years ago"
    assert utils.relative_time("2024-06-01T12:00:00Z", now) == "just now"  # future date


# ------------------------------ Numbers -------------------------------------- #
def test_format_number():
    assert utils.format_number(1234567) == "1,234,567"
    assert utils.format_number("42") == "42"
    for bad in (None, "x", True):
        assert utils.format_number(bad) == "0"


def test_format_bytes_and_kb():
    assert utils.format_bytes(0) == "0 B"
    assert utils.format_bytes(512) == "512 B"
    assert utils.format_bytes(2048) == "2.0 KB"
    assert utils.format_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert utils.format_bytes(None) == "0 B"
    assert utils.format_bytes(-1) == "0 B"
    assert utils.format_size_kb(512) == "512.0 KB"
    assert utils.format_size_kb(2048) == "2.0 MB"
    assert utils.format_size_kb("bad") == "0 B"


# ----------------------------- Validation ------------------------------------ #
@pytest.mark.parametrize("name", ["octocat", "a", "Torvalds", "my-user-1", "a" * 39])
def test_valid_usernames(name):
    assert utils.validate_username(name) == (True, "")


@pytest.mark.parametrize("name", [None, "", "   ", "-bad", "bad-", "do--uble", "has space",
                                  "under_score", "a" * 40, "emoji😀", "../etc"])
def test_invalid_usernames(name):
    ok, message = utils.validate_username(name)
    assert ok is False and message


def test_clean_username_accepts_urls_and_at_sign():
    assert utils.clean_username("  @octocat ") == "octocat"
    assert utils.clean_username("https://github.com/octocat") == "octocat"
    assert utils.clean_username("github.com/octocat/Hello-World") == "octocat"
    assert utils.validate_username("https://github.com/octocat")[0] is True


# --------------------------- Safe text and URLs ------------------------------ #
def test_safe_text_escapes_html_and_collapses_whitespace():
    assert utils.safe_text("<script>alert(1)</script>") == "&lt;script&gt;alert&#40;1&#41;&lt;/script&gt;"
    assert "<" not in utils.safe_text('<img src=x onerror="bad()">')
    assert '"' not in utils.safe_text('say "hi"')
    assert utils.safe_text("line one\n\nline   two") == "line one line two"
    assert utils.safe_text(None) == ""
    assert utils.safe_text("x" * 20, max_length=10).endswith("…")


def test_safe_text_neutralizes_markdown():
    text = utils.safe_text("**bold** _it_ ![x](http://evil.example/p.png) [a](b) `code`")
    for char in "*_[]()!`":
        assert char not in text
    assert "&#42;&#42;bold&#42;&#42;" in text


def test_normalize_url():
    assert utils.normalize_url("https://example.com/a") == "https://example.com/a"
    assert utils.normalize_url("example.com") == "https://example.com"
    assert utils.normalize_url("localhost.test:8080/x") == "https://localhost.test:8080/x"
    for bad in (None, "", "javascript:alert(1)", "data:text/html,hi", "ftp://x.com",
                "https://", "has space.com", 123):
        assert utils.normalize_url(bad) is None


def test_github_and_avatar_url_checks():
    assert utils.github_url("https://github.com/octocat/repo") == "https://github.com/octocat/repo"
    assert utils.github_url("https://evil.example/github.com") is None
    assert utils.avatar_url("https://avatars.githubusercontent.com/u/583231?v=4")
    assert utils.avatar_url("https://evil.example/avatar.png") is None
    assert utils.avatar_url("https://githubusercontent.com.evil.example/x.png") is None


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nNEW_TEST_KEY=abc\nEXISTING_TEST_KEY=changed\nEMPTY_TEST_KEY=\n")
    monkeypatch.delenv("NEW_TEST_KEY", raising=False)
    monkeypatch.delenv("EMPTY_TEST_KEY", raising=False)
    monkeypatch.setenv("EXISTING_TEST_KEY", "original")
    utils.load_env_file(env_file)
    import os
    assert os.environ["NEW_TEST_KEY"] == "abc"
    assert os.environ["EXISTING_TEST_KEY"] == "original"
    assert "EMPTY_TEST_KEY" not in os.environ
    utils.load_env_file(tmp_path / "missing.env")  # must not raise
    monkeypatch.delenv("NEW_TEST_KEY", raising=False)
