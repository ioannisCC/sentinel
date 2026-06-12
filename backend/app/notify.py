"""D09 — notify external surfaces when the watcher fires a re-audit.

We dispatch a GitHub issue via Composio's GITHUB_CREATE_AN_ISSUE tool on the
sentinel repo so the demo room sees a real cross-system action triggered by
an autonomous claim-drift detection.

Honesty: if no GitHub account is connected in the Composio dashboard, the
SDK call will return an error response — we LOG IT HONESTLY (no fake
"success"). The dispatch line was explicit: "if not connected, log the
401 honestly — don't fake the post."

Fire-and-forget: never blocks the pipeline, never raises."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings


log = logging.getLogger("sentinel.notify")


@dataclass(frozen=True)
class SentinelDelta:
    """The change envelope handed to notify(). Compact on purpose — Composio
    actions take short payloads better than full MarketResult dumps."""
    vendor: str
    url: str
    old_score: Optional[float]
    new_score: Optional[float]
    published_url: Optional[str] = None


# Repo coordinates. The dispatch named the sentinel repo; we look it up via
# the running git remote at import time so a fork ships an issue to its own
# fork by default. Falls back to the canonical ioannisCC/sentinel.
def _resolve_repo() -> tuple[str, str]:
    import subprocess
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(settings.model_config.get("env_file", ".")).rsplit("/", 1)[0],
            timeout=2.0, text=True,
        ).strip()
        # e.g. https://github.com/ioannisCC/sentinel.git or git@github.com:owner/repo.git
        if "github.com" in remote:
            tail = remote.split("github.com", 1)[1].lstrip(":/").removesuffix(".git")
            owner, _, repo = tail.partition("/")
            if owner and repo:
                return owner, repo
    except Exception:  # noqa: BLE001
        pass
    return ("ioannisCC", "sentinel")


_REPO_OWNER, _REPO_NAME = _resolve_repo()


def _format_issue(delta: SentinelDelta) -> tuple[str, str]:
    """Title + markdown body for the GitHub issue."""
    old = "—" if delta.old_score is None else f"{round(delta.old_score * 100)}%"
    new = "—" if delta.new_score is None else f"{round(delta.new_score * 100)}%"
    arrow = ""
    if delta.old_score is not None and delta.new_score is not None:
        if delta.new_score < delta.old_score:
            arrow = " ↓"
        elif delta.new_score > delta.old_score:
            arrow = " ↑"
    title = f"Sentinel — claim drift detected: {delta.vendor} ({old} → {new}{arrow})"
    body_lines = [
        f"**Vendor**: {delta.vendor}",
        f"**Marketing page**: {delta.url}",
        f"**Score**: {old} → {new}{arrow}",
        "",
        "Sentinel's autonomous watch loop detected a change in this vendor's "
        "public marketing claims and re-audited against public web evidence. "
        "We measure **public substantiation**, never truth.",
    ]
    if delta.published_url:
        body_lines += [
            "",
            f"**Published audit**: {delta.published_url}",
        ]
    return title, "\n".join(body_lines)


async def _execute_create_issue(delta: SentinelDelta) -> None:
    """Call Composio's GITHUB_CREATE_AN_ISSUE in a worker thread (the SDK is
    sync). Log the outcome verbatim; never raise."""
    from composio import Composio

    title, body = _format_issue(delta)
    try:
        client = Composio(api_key=settings.COMPOSIO_API_KEY)
        resp = await asyncio.to_thread(
            client.tools.execute,
            "GITHUB_CREATE_AN_ISSUE",
            {
                "owner": _REPO_OWNER,
                "repo": _REPO_NAME,
                "title": title,
                "body": body,
            },
            user_id="sentinel",
            # Composio's new SDK requires an explicit toolkit version. We bypass
            # the strict check so this keeps working when GitHub's toolkit ships
            # a new version — the alternative is hard-coding "20260501_01"
            # which goes stale on the next Composio toolkit bump.
            dangerously_skip_version_check=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("notify failed (composio raised): %s", e)
        return

    # The SDK returns a structured response; success/error lands on attrs
    # we can't fully type without committing to the alpha SDK shape, so we
    # peek defensively + log verbatim.
    successful = getattr(resp, "successful", None)
    if successful is None:
        # Some response variants put it under .data
        data = getattr(resp, "data", None) or {}
        successful = bool(data.get("successful")) if isinstance(data, dict) else None
    error = getattr(resp, "error", None) or getattr(resp, "message", None)

    if successful:
        # Extract issue URL when present
        data = getattr(resp, "data", None) or {}
        issue_url = None
        if isinstance(data, dict):
            issue_url = (
                data.get("html_url")
                or (data.get("response_data") or {}).get("html_url")
                or (data.get("issue") or {}).get("html_url")
            )
        log.info(
            "notify ok: GITHUB_CREATE_AN_ISSUE for %s/%s → %s",
            _REPO_OWNER, _REPO_NAME, issue_url or "<no url in response>",
        )
    else:
        log.warning(
            "notify error: GITHUB_CREATE_AN_ISSUE %s/%s — error=%s "
            "(check that GitHub is connected in the Composio dashboard for user_id=sentinel)",
            _REPO_OWNER, _REPO_NAME, error,
        )


async def notify(delta: SentinelDelta) -> None:
    """Fire a GitHub issue on the sentinel repo for one claim-drift event.
    No-op when COMPOSIO_API_KEY is unset; honest error log when GitHub
    isn't connected for the Composio user. Never raises."""
    if not settings.COMPOSIO_API_KEY:
        log.warning(
            "notify skipped: no key (COMPOSIO_API_KEY unset; vendor=%s %s→%s)",
            delta.vendor, delta.old_score, delta.new_score,
        )
        return
    # Spawn so the publish path never waits on Composio's round-trip.
    asyncio.create_task(_execute_create_issue(delta))
