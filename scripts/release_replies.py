#!/usr/bin/env python3
"""Post the daily-release replies drafted by Claude, under shell control.

The Claude steps of ``.github/workflows/daily-release.yml`` only draft text
(``replies.json`` and ``notes.md``) with a read-only token. This script is the
only code that holds the admin token: it vets every drafted reply, posts it,
closes threads strictly as the maintainer queue (``docs/release-replies.md``)
dictates, and clears the processed queue entries.

Subcommands:
  targets       Print the thread numbers a reply may go to, one per line.
  check-drafts  Fail unless a drafts directory holds exactly a clean notes.md
                and a schema-valid replies.json.
  post          Post vetted replies, close queued threads, clear the queue.
  check-notes   Fail when a notes file contains disallowed content.

Pure standard library; subprocess calls use list arguments, never a shell.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

QUEUE_MARKER = "<!-- entries below"
CLOSE_VALUES = ("yes", "no", "duplicate", "declined")
REPO_PATH = "/lasswellt/govee-homeassistant"
ALLOWED_HOSTS = ("my.home-assistant.io",)
SHELL_LANGS = {"sh", "bash", "shell", "console", "zsh"}
MAX_BODY = 4000
MAX_REPLIES = 200
DRAFT_FILES = frozenset({"notes.md", "replies.json"})
COMMIT_MESSAGE = "chore: clear release reply queue"

_ISSUE_REF = re.compile(r"#(\d+)")
_ENTRY_HEAD = re.compile(r"^##\s+#(\d+)\s*$", re.MULTILINE)
_CLOSE_LINE = re.compile(r"^\s*(?:[-*]\s*)?close:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_BRIEF_LINE = re.compile(r"^\s*(?:[-*]\s*)?brief:\s*(.*)", re.MULTILINE | re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HIDDEN_CHARS = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_URL = re.compile(r"https?://[^\s)>\]\"'<]+", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)", re.MULTILINE)
_PROMPT_LINE = re.compile(r"^\s*\$ ", re.MULTILINE)
# Credential shapes that must never be published: Anthropic keys and OAuth
# tokens (the Claude job holds CLAUDE_CODE_OAUTH_TOKEN), GitHub tokens, and
# long base64-like runs (certificates, JWTs) mixing upper, lower and digits.
_SECRET_PREFIX = re.compile(r"sk-ant-|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_", re.IGNORECASE)
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{48,}")


@dataclass(frozen=True)
class QueueEntry:
    """One ``## #N`` section of the maintainer reply queue.

    Attributes:
        number: Issue or PR number.
        close: One of ``yes``, ``no``, ``duplicate``, ``declined``.
        brief: What the reply must say.
        raw: The section text as written, used to remove it from the queue.
    """

    number: int
    close: str
    brief: str
    raw: str = ""


@dataclass(frozen=True)
class PlannedAction:
    """A reply the script intends to post.

    Attributes:
        number: Target issue or PR number.
        body: Sanitized comment body (empty when skipped).
        skip_reason: Why it is skipped, or None when it will be posted.
        close: Close value from the queue (``no`` when not queued).
    """

    number: int
    body: str
    skip_reason: str | None
    close: str


def allowed_targets(subjects: list[str], queue_text: str) -> set[int]:
    """Collect the threads a reply may be posted on.

    Args:
        subjects: ALL commit subjects since the last tag (not path-limited, so
            merge commits such as ``Merge PR #238: ... (#237)`` count).
        queue_text: Contents of ``docs/release-replies.md``.

    Returns:
        Every ``#N`` in the subjects plus every queue entry number.
    """
    targets = {int(n) for subject in subjects for n in _ISSUE_REF.findall(subject)}
    targets.update(entry.number for entry in parse_queue(queue_text))
    return targets


def parse_queue(text: str) -> list[QueueEntry]:
    """Parse the queue entries below the ``<!-- entries below`` marker.

    Args:
        text: Contents of ``docs/release-replies.md``.

    Returns:
        The entries in file order. An unknown or missing ``close`` is ``no``.
    """
    idx = text.find(QUEUE_MARKER)
    if idx < 0:
        return []
    body = text[idx:]
    body = body[body.find("\n") + 1 :] if "\n" in body else ""
    heads = list(_ENTRY_HEAD.finditer(body))
    entries: list[QueueEntry] = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        raw = body[head.start() : end]
        close_match = _CLOSE_LINE.search(raw)
        close = close_match.group(1).lower().strip("`.,") if close_match else "no"
        brief_match = _BRIEF_LINE.search(raw)
        entries.append(
            QueueEntry(
                number=int(head.group(1)),
                close=close if close in CLOSE_VALUES else "no",
                brief=brief_match.group(1).strip() if brief_match else "",
                raw=raw,
            )
        )
    return entries


def sanitize(body: str) -> str:
    """Strip HTML comments and zero-width or bidi control characters.

    Args:
        body: Drafted comment text.

    Returns:
        The text without hidden content.
    """
    return _HIDDEN_CHARS.sub("", _HTML_COMMENT.sub("", body))


def reject_reason(body: str) -> str | None:
    """Say why a drafted text must not be published, if it must not.

    Rejects URLs outside this repository and my.home-assistant.io, credentials
    and credential-like strings, shell-marked code fences, and ``$ `` prompt
    lines. The reason never echoes the text.

    Args:
        body: Reply or release-notes text.

    Returns:
        A short reason, or None when the text is acceptable.
    """
    for url in _URL.findall(body):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in ALLOWED_HOSTS:
            continue
        if host == "github.com" and (parsed.path == REPO_PATH or parsed.path.startswith(REPO_PATH + "/")):
            continue
        return "contains a URL outside the allowed hosts"
    if _SECRET_PREFIX.search(body):
        return "contains a credential"
    for run in _LONG_RUN.findall(_URL.sub(" ", body)):
        if re.search(r"[A-Z]", run) and re.search(r"[a-z]", run) and re.search(r"\d", run):
            return "contains a credential-like string"
    for fence in _FENCE.finditer(body):
        if fence.group(2).lower() in SHELL_LANGS:
            return "contains a shell code block"
    if _PROMPT_LINE.search(body):
        return "contains a shell prompt line"
    return None


def plan_actions(replies: list[object], entries: list[QueueEntry], allowed: set[int]) -> list[PlannedAction]:
    """Decide which drafted replies get posted.

    Args:
        replies: Decoded ``replies.json`` (a list of ``{"number", "body"}``).
        entries: Parsed queue entries; they alone decide closing.
        allowed: Output of :func:`allowed_targets`.

    Returns:
        One action per drafted reply, with a skip reason when it is refused.
    """
    close_by_number = {e.number: e.close for e in entries}
    actions: list[PlannedAction] = []
    seen: set[int] = set()
    for item in replies:
        if not isinstance(item, dict):
            actions.append(PlannedAction(0, "", "malformed reply item", "no"))
            continue
        number, body = item.get("number"), item.get("body")
        if isinstance(number, bool) or not isinstance(number, int) or not isinstance(body, str):
            actions.append(PlannedAction(0, "", "malformed reply item", "no"))
            continue
        clean = sanitize(body)
        reason: str | None = None
        if number in seen:
            reason = "duplicate draft"
        elif number not in allowed:
            reason = "number is not a released or queued thread"
        elif not clean.strip():
            reason = "empty body"
        elif len(clean) > MAX_BODY:
            reason = f"body longer than {MAX_BODY} characters"
        else:
            reason = reject_reason(clean)
        seen.add(number)
        actions.append(PlannedAction(number, "" if reason else clean, reason, close_by_number.get(number, "no")))
    return actions


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and capture its output."""
    return subprocess.run(args, check=False, capture_output=True, text=True)


def _close_thread(number: int, close: str) -> bool:
    """Close a thread as the queue directs.

    Args:
        number: Issue or PR number.
        close: ``yes``, ``duplicate`` or ``declined``.

    Returns:
        True when the close command succeeded.
    """
    if _run(["gh", "pr", "view", str(number)]).returncode == 0:
        return _run(["gh", "pr", "close", str(number)]).returncode == 0
    reason = "completed" if close == "yes" else "not planned"
    return _run(["gh", "issue", "close", str(number), "--reason", reason]).returncode == 0


def remove_entries(text: str, done: list[QueueEntry]) -> str:
    """Drop processed entries from the queue, keeping header and marker.

    Args:
        text: Contents of ``docs/release-replies.md``.
        done: Entries to remove.

    Returns:
        The updated queue text.
    """
    for entry in done:
        text = text.replace(entry.raw, "", 1)
    return text


def run_post(replies: list[object], queue_text: str, subjects: list[str], dry_run: bool) -> tuple[str, list[str]]:
    """Post vetted replies, close queued threads and compute the new queue.

    Args:
        replies: Decoded ``replies.json``.
        queue_text: Contents of ``docs/release-replies.md``.
        subjects: Commit subjects since the last tag.
        dry_run: Print the plan and call nothing.

    Returns:
        The new queue text and the printed log lines.
    """
    entries = parse_queue(queue_text)
    actions = plan_actions(replies, entries, allowed_targets(subjects, queue_text))
    log: list[str] = []

    def say(line: str) -> None:
        log.append(line)
        print(line)

    posted: set[int] = set()
    for action in actions:
        if action.skip_reason:
            say(f"skip #{action.number or '?'}: {action.skip_reason}")
            continue
        if dry_run:
            say(f"would comment on #{action.number}")
            if action.close != "no":
                say(f"would close #{action.number} (close: {action.close})")
            posted.add(action.number)
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8") as handle:
            handle.write(action.body)
            handle.flush()
            result = _run(["gh", "issue", "comment", str(action.number), "--body-file", handle.name])
        if result.returncode != 0:
            say(f"failed to comment on #{action.number}; entry kept")
            continue
        say(f"commented on #{action.number}")
        posted.add(action.number)
        if action.close != "no":
            ok = _close_thread(action.number, action.close)
            say(f"{'closed' if ok else 'failed to close'} #{action.number}")
    done = [e for e in entries if e.number in posted]
    return remove_entries(queue_text, done), log


def _git_push() -> bool:
    """Commit the cleared queue and push it with the token from GH_TOKEN."""
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GH_TOKEN or GITHUB_REPOSITORY missing; queue not pushed", file=sys.stderr)
        return False
    if _run(["git", "add", "docs/release-replies.md"]).returncode != 0:
        return False
    if _run(["git", "commit", "-m", COMMIT_MESSAGE]).returncode != 0:
        return False
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    return _run(["git", "push", url, "HEAD:main"]).returncode == 0


def check_notes(path: Path) -> str | None:
    """Vet a release-notes file.

    Args:
        path: The notes file.

    Returns:
        A reason to refuse publishing it, or None.
    """
    text = path.read_text(encoding="utf-8")
    if sanitize(text) != text:
        return "contains hidden content"
    return reject_reason(text)


def replies_schema_error(replies: object) -> str | None:
    """Check decoded ``replies.json`` against its schema.

    Args:
        replies: The decoded file.

    Returns:
        What is wrong, or None for a list of at most MAX_REPLIES objects with
        exactly a positive int ``number`` and a str ``body``.
    """
    if not isinstance(replies, list):
        return "must be a JSON list"
    if len(replies) > MAX_REPLIES:
        return f"has more than {MAX_REPLIES} items"
    for item in replies:
        if not isinstance(item, dict) or set(item) != {"number", "body"}:
            return "items must be objects with exactly number and body"
        number = item["number"]
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            return "number must be a positive integer"
        if not isinstance(item["body"], str):
            return "body must be a string"
    return None


def check_drafts(directory: Path) -> str | None:
    """Vet the draft job's artifact before the token job uses it.

    Args:
        directory: Where the ``release-drafts`` artifact was downloaded.

    Returns:
        A reason to refuse the drafts, or None.
    """
    entries = {p.name: p for p in directory.iterdir()} if directory.is_dir() else {}
    if set(entries) != DRAFT_FILES:
        return "the drafts must be exactly notes.md and replies.json"
    for path in entries.values():
        if path.is_symlink() or not path.is_file():
            return f"{path.name} is not a regular file"
    notes = entries["notes.md"]
    if not notes.read_text(encoding="utf-8").strip():
        return "notes.md is empty"
    reason = check_notes(notes)
    if reason:
        return f"notes.md {reason}"
    try:
        replies = json.loads(entries["replies.json"].read_text(encoding="utf-8"))
    except ValueError:
        return "replies.json is not valid JSON"
    error = replies_schema_error(replies)
    return f"replies.json {error}" if error else None


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    post = sub.add_parser("post", help="post vetted replies and clear the queue")
    post.add_argument("--replies", type=Path, required=True, help="replies.json drafted by Claude")
    post.add_argument("--subjects-file", type=Path, required=True, help="`git log <tag>..HEAD --format=%%s` output")
    post.add_argument("--queue", type=Path, default=Path("docs/release-replies.md"))
    post.add_argument("--dry-run", action="store_true", help="print the plan and call nothing")
    notes = sub.add_parser("check-notes", help="fail on disallowed release-notes content")
    notes.add_argument("file", type=Path)
    targets = sub.add_parser("targets", help="print the threads a reply may go to")
    targets.add_argument("--subjects-file", type=Path, required=True, help="`git log <tag>..HEAD --format=%%s` output")
    targets.add_argument("--queue", type=Path, default=Path("docs/release-replies.md"))
    drafts = sub.add_parser("check-drafts", help="fail unless the drafts directory is clean")
    drafts.add_argument("directory", type=Path)
    args = parser.parse_args(argv)

    if args.command == "targets":
        subjects = args.subjects_file.read_text(encoding="utf-8").splitlines()
        for number in sorted(allowed_targets(subjects, args.queue.read_text(encoding="utf-8"))):
            print(number)
        return 0
    if args.command == "check-drafts":
        reason = check_drafts(args.directory)
        if reason:
            print(f"::error::drafts rejected: {reason}")
            return 1
        return 0
    if args.command == "check-notes":
        reason = check_notes(args.file)
        if reason:
            print(f"::error::release notes rejected: {reason}")
            return 1
        return 0

    try:
        replies = json.loads(args.replies.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("::error::replies.json is missing or not valid JSON")
        return 1
    error = replies_schema_error(replies)
    if error:
        print(f"::error::replies.json {error}")
        return 1
    queue_text = args.queue.read_text(encoding="utf-8")
    subjects = args.subjects_file.read_text(encoding="utf-8").splitlines()
    new_text, _ = run_post(replies, queue_text, subjects, args.dry_run)
    if args.dry_run:
        if new_text != queue_text:
            print("would clear processed queue entries, commit and push")
        return 0
    if new_text != queue_text:
        args.queue.write_text(new_text, encoding="utf-8")
        if not _git_push():
            print("::error::could not push the cleared queue")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
