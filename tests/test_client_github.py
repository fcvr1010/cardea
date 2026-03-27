"""
Tests for the GitHub client module (cardea.client.github).

Uses respx to mock httpx requests — no server or network needed.
"""

import json

import httpx
import respx
from httpx import Response

from cardea.client.github import (
    create_pr,
    delete_branch,
    get_pr,
    github_api,
    list_prs,
    merge_pr,
)

BASE = "http://localhost:8000"


# -- github_api --------------------------------------------------------------


@respx.mock
def test_github_api_get():
    """github_api makes a GET request to the correct path."""
    route = respx.get(f"{BASE}/github/api/repos/owner/repo").mock(
        return_value=Response(200, json={"full_name": "owner/repo"})
    )
    result = github_api("repos/owner/repo")
    assert result == {"full_name": "owner/repo"}
    assert route.called


@respx.mock
def test_github_api_strips_leading_slash():
    """github_api strips leading slashes from the endpoint."""
    route = respx.get(f"{BASE}/github/api/repos/owner/repo").mock(
        return_value=Response(200, json={})
    )
    github_api("/repos/owner/repo")
    assert route.called


@respx.mock
def test_github_api_post_with_body():
    """github_api can POST with a JSON body."""
    route = respx.post(f"{BASE}/github/api/repos/o/r/issues").mock(
        return_value=Response(201, json={"number": 1})
    )
    result = github_api("repos/o/r/issues", method="POST", json_body={"title": "Bug"})
    assert result == {"number": 1}
    body = json.loads(route.calls[0].request.content)
    assert body["title"] == "Bug"


@respx.mock
def test_github_api_with_params():
    """github_api passes query params for GET requests."""
    route = respx.get(
        f"{BASE}/github/api/repos/o/r/issues", params={"state": "open"}
    ).mock(return_value=Response(200, json=[]))
    result = github_api("repos/o/r/issues", params={"state": "open"})
    assert result == []
    assert route.called


@respx.mock
def test_github_api_custom_base_url():
    """github_api with explicit base_url."""
    route = respx.get("http://custom:9000/github/api/user").mock(
        return_value=Response(200, json={"login": "test"})
    )
    result = github_api("user", base_url="http://custom:9000")
    assert result == {"login": "test"}
    assert route.called


# -- list_prs ----------------------------------------------------------------


@respx.mock
def test_list_prs():
    """list_prs sends GET with state and per_page params."""
    route = respx.get(
        f"{BASE}/github/api/repos/o/r/pulls",
        params={"state": "open", "per_page": 10},
    ).mock(return_value=Response(200, json=[{"number": 1}]))
    result = list_prs("o", "r")
    assert result == [{"number": 1}]
    assert route.called


@respx.mock
def test_list_prs_custom_state():
    """list_prs passes custom state and per_page."""
    route = respx.get(
        f"{BASE}/github/api/repos/o/r/pulls",
        params={"state": "closed", "per_page": 5},
    ).mock(return_value=Response(200, json=[]))
    result = list_prs("o", "r", state="closed", per_page=5)
    assert result == []
    assert route.called


# -- get_pr ------------------------------------------------------------------


@respx.mock
def test_get_pr():
    """get_pr sends GET /repos/{owner}/{repo}/pulls/{number}."""
    route = respx.get(f"{BASE}/github/api/repos/o/r/pulls/42").mock(
        return_value=Response(200, json={"number": 42, "title": "Fix bug"})
    )
    result = get_pr("o", "r", 42)
    assert result["number"] == 42
    assert result["title"] == "Fix bug"
    assert route.called


# -- create_pr ---------------------------------------------------------------


@respx.mock
def test_create_pr():
    """create_pr sends POST with title, head, base, body."""
    route = respx.post(f"{BASE}/github/api/repos/o/r/pulls").mock(
        return_value=Response(201, json={"number": 10, "html_url": "http://..."})
    )
    result = create_pr("o", "r", title="New PR", head="feature")
    assert result["number"] == 10
    body = json.loads(route.calls[0].request.content)
    assert body["title"] == "New PR"
    assert body["head"] == "feature"
    assert body["base"] == "main"
    assert body["body"] == ""


@respx.mock
def test_create_pr_custom_base_and_body():
    """create_pr with custom base and body."""
    route = respx.post(f"{BASE}/github/api/repos/o/r/pulls").mock(
        return_value=Response(201, json={"number": 11})
    )
    create_pr("o", "r", title="PR", head="feat", base="develop", body="Description")
    body = json.loads(route.calls[0].request.content)
    assert body["base"] == "develop"
    assert body["body"] == "Description"


# -- merge_pr ----------------------------------------------------------------


@respx.mock
def test_merge_pr():
    """merge_pr sends PUT with merge_method."""
    route = respx.put(f"{BASE}/github/api/repos/o/r/pulls/5/merge").mock(
        return_value=Response(200, json={"sha": "abc", "merged": True, "message": "ok"})
    )
    result = merge_pr("o", "r", 5)
    assert result["merged"] is True
    body = json.loads(route.calls[0].request.content)
    assert body["merge_method"] == "squash"


@respx.mock
def test_merge_pr_with_commit_title_and_message():
    """merge_pr passes commit_title and commit_message when provided."""
    route = respx.put(f"{BASE}/github/api/repos/o/r/pulls/7/merge").mock(
        return_value=Response(200, json={"sha": "def", "merged": True, "message": "ok"})
    )
    merge_pr("o", "r", 7, commit_title="Title", commit_message="Body")
    body = json.loads(route.calls[0].request.content)
    assert body["commit_title"] == "Title"
    assert body["commit_message"] == "Body"


# -- delete_branch -----------------------------------------------------------


@respx.mock
def test_delete_branch():
    """delete_branch sends DELETE and returns synthesised dict."""
    route = respx.delete(
        f"{BASE}/github/api/repos/o/r/git/refs/heads/feature-branch"
    ).mock(return_value=Response(204))
    result = delete_branch("o", "r", "feature-branch")
    assert result == {"deleted": True, "branch": "feature-branch"}
    assert route.called


# -- Error handling ----------------------------------------------------------


@respx.mock
def test_get_pr_raises_on_404():
    """get_pr raises HTTPStatusError on 404."""
    respx.get(f"{BASE}/github/api/repos/o/r/pulls/999").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )
    try:
        get_pr("o", "r", 999)
        raise AssertionError("Expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 404
