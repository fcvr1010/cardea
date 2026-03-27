"""Cardea client library -- HTTP clients for Cardea proxy services.

Install::

    pip install cardea[client]

Quick start::

    from cardea.client.email import send_email, list_messages
    from cardea.client.github import github_api, create_pr
    from cardea.client.browser import fill_credentials

Or import everything from the top-level namespace::

    from cardea.client import send_email, github_api, fill_credentials

Set the ``CARDEA_URL`` environment variable to point at your Cardea
instance (default: ``http://localhost:8000``), or pass ``base_url``
explicitly to any function.
"""

from cardea.client.browser import fill_credentials
from cardea.client.email import list_messages, read_message, reply_email, send_email
from cardea.client.github import (
    create_pr,
    delete_branch,
    get_pr,
    github_api,
    list_prs,
    merge_pr,
)

__all__ = [
    "create_pr",
    "delete_branch",
    "fill_credentials",
    "get_pr",
    "github_api",
    "list_messages",
    "list_prs",
    "merge_pr",
    "read_message",
    "reply_email",
    "send_email",
]
