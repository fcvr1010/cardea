"""
GitHub client for Cardea — interact with the GitHub API through the proxy.

    github_api(endpoint, method="GET", params=None, json_body=None, base_url=None) -> dict | list
    list_prs(owner, repo, state="open", per_page=10, base_url=None) -> list[dict]
    get_pr(owner, repo, pr_number, base_url=None) -> dict
    create_pr(owner, repo, title, head, base="main", body="", base_url=None) -> dict
    merge_pr(owner, repo, pr_number, merge_method="squash", commit_title=None, commit_message=None, base_url=None) -> dict
    delete_branch(owner, repo, branch, base_url=None) -> dict

Server endpoint: ``/github/api/{path}`` — generic proxy to ``api.github.com``.
Cardea injects the GitHub token automatically.

``github_api`` is the low-level escape hatch for any GitHub API call.
The other functions are convenience wrappers for common PR operations.

Return values:

- ``list_prs``: ``[{number, title, state, user, html_url, ...}, ...]``
- ``get_pr``: ``{number, title, state, body, user, html_url, mergeable, ...}``
- ``create_pr``: ``{number, title, html_url, ...}``
- ``merge_pr``: ``{sha, merged, message}``
- ``delete_branch``: ``{deleted: True, branch: "<name>"}``
"""

from __future__ import annotations

from typing import Any

from cardea.client._base import _request, _resolve_base_url


def github_api(
    endpoint: str,
    method: str = "GET",
    params: dict[str, str | int] | None = None,
    json_body: dict[str, object] | None = None,
    base_url: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Make an arbitrary GitHub API call through the Cardea proxy.

    Args:
        endpoint: API path without the base URL, e.g. ``"repos/owner/repo"``.
                  Leading slash is optional.
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        params: Optional query parameters (for GET requests).
        json_body: Optional JSON body (for POST/PUT/PATCH requests).
        base_url: Override the Cardea server URL.

    Returns:
        Parsed JSON response (dict or list of dicts).
    """
    base = _resolve_base_url(base_url)
    endpoint = endpoint.lstrip("/")
    response = _request(
        method,
        f"{base}/github/api/{endpoint}",
        params=params,
        json=json_body,
    )
    result: dict[str, Any] | list[dict[str, Any]] = response.json()
    return result


def list_prs(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 10,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """List pull requests for a repository.

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        state: PR state filter — ``"open"``, ``"closed"``, or ``"all"``.
        per_page: Number of results per page (default 10).
        base_url: Override the Cardea server URL.

    Returns:
        List of PR dicts with keys like ``number``, ``title``, ``state``,
        ``user``, ``html_url``, ``created_at``, ``updated_at``.
    """
    base = _resolve_base_url(base_url)
    params: dict[str, str | int] = {"state": state, "per_page": per_page}
    response = _request(
        "GET",
        f"{base}/github/api/repos/{owner}/{repo}/pulls",
        params=params,
    )
    result: list[dict[str, Any]] = response.json()
    return result


def get_pr(
    owner: str,
    repo: str,
    pr_number: int,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Get details of a single pull request.

    Args:
        owner: Repository owner.
        repo: Repository name.
        pr_number: Pull request number.
        base_url: Override the Cardea server URL.

    Returns:
        PR dict with keys like ``number``, ``title``, ``state``, ``body``,
        ``user``, ``html_url``, ``mergeable``.
    """
    base = _resolve_base_url(base_url)
    response = _request(
        "GET",
        f"{base}/github/api/repos/{owner}/{repo}/pulls/{pr_number}",
    )
    result: dict[str, Any] = response.json()
    return result


def create_pr(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str = "main",
    body: str = "",
    base_url: str | None = None,
) -> dict[str, Any]:
    """Create a new pull request.

    Args:
        owner: Repository owner.
        repo: Repository name.
        title: PR title.
        head: Branch containing the changes.
        base: Branch to merge into (default ``"main"``).
        body: PR description/body text.
        base_url: Override the Cardea server URL.

    Returns:
        Created PR dict with keys like ``number``, ``title``, ``html_url``.
    """
    resolved_base_url = _resolve_base_url(base_url)
    payload: dict[str, object] = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    response = _request(
        "POST",
        f"{resolved_base_url}/github/api/repos/{owner}/{repo}/pulls",
        json=payload,
    )
    result: dict[str, Any] = response.json()
    return result


def merge_pr(
    owner: str,
    repo: str,
    pr_number: int,
    merge_method: str = "squash",
    commit_title: str | None = None,
    commit_message: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Merge a pull request.

    Args:
        owner: Repository owner.
        repo: Repository name.
        pr_number: Pull request number.
        merge_method: Merge strategy — ``"merge"``, ``"squash"``, or
                      ``"rebase"`` (default ``"squash"``).
        commit_title: Custom commit title (optional).
        commit_message: Custom commit message body (optional).
        base_url: Override the Cardea server URL.

    Returns:
        Dict with keys ``sha``, ``merged``, ``message``.
    """
    resolved_base_url = _resolve_base_url(base_url)
    payload: dict[str, object] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title
    if commit_message:
        payload["commit_message"] = commit_message
    response = _request(
        "PUT",
        f"{resolved_base_url}/github/api/repos/{owner}/{repo}/pulls/{pr_number}/merge",
        json=payload,
    )
    result: dict[str, Any] = response.json()
    return result


def delete_branch(
    owner: str,
    repo: str,
    branch: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Delete a remote branch (typically after PR merge).

    Args:
        owner: Repository owner.
        repo: Repository name.
        branch: Branch name to delete.
        base_url: Override the Cardea server URL.

    Returns:
        ``{deleted: True, branch: "<name>"}`` on success.

    Note:
        GitHub returns 204 No Content on success. This function
        synthesises a dict response for consistency.
    """
    resolved_base_url = _resolve_base_url(base_url)
    _request(
        "DELETE",
        f"{resolved_base_url}/github/api/repos/{owner}/{repo}/git/refs/heads/{branch}",
    )
    # 204 No Content = success, no JSON body
    return {"deleted": True, "branch": branch}
