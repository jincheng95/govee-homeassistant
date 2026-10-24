"""Tests for the daily-release workflow hardening and scripts/release_replies.py."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "daily-release.yml"

_spec = importlib.util.spec_from_file_location("release_replies", ROOT / "scripts" / "release_replies.py")
assert _spec is not None and _spec.loader is not None
rr = importlib.util.module_from_spec(_spec)
sys.modules["release_replies"] = rr
_spec.loader.exec_module(rr)

OUT_RULE = "Edit(/${{ runner.temp }}/out/**)"
ALLOWED_TOOLS = ["Read", OUT_RULE]


@pytest.fixture(scope="module")
def jobs() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data["jobs"]


def _claude_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")]


APP_TOKEN_ACTION = "actions/create-github-app-token@"
APP_SECRETS = ("secrets.RELEASE_APP_CLIENT_ID", "secrets.RELEASE_APP_PRIVATE_KEY")


def _mint_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith(APP_TOKEN_ACTION)]


def _uses_app_token(step: dict[str, Any]) -> bool:
    return "outputs.token" in str(step.get("env", {}))


class TestWorkflow:
    def test_jobs(self, jobs: dict[str, Any]) -> None:
        assert set(jobs) == {"gate", "draft", "preview", "publish"}

    def test_claude_runs_only_in_the_draft_job(self, jobs: dict[str, Any]) -> None:
        assert len(_claude_steps(jobs["draft"])) == 2
        for name in ("gate", "preview", "publish"):
            assert _claude_steps(jobs[name]) == []

    def test_no_personal_access_token(self) -> None:
        assert "RELEASE_TOKEN" not in WORKFLOW.read_text(encoding="utf-8")

    def test_app_key_only_in_publish_mint_steps(self, jobs: dict[str, Any]) -> None:
        for name in ("gate", "draft", "preview"):
            assert "RELEASE_APP" not in str(jobs[name])
            assert "environment" not in jobs[name]
        publish = jobs["publish"]
        assert publish["environment"] == "release"
        mints = _mint_steps(publish)
        assert [s["id"] for s in mints] == ["push-token", "release-token"]
        for step in publish["steps"]:
            if step in mints:
                assert [step["with"]["client-id"], step["with"]["private-key"]] == [
                    "${{ " + secret + " }}" for secret in APP_SECRETS
                ]
            else:
                assert "RELEASE_APP" not in str(step)
        assert all("GH_TOKEN" not in job.get("env", {}) for job in jobs.values())

    def test_app_tokens_are_narrowed(self, jobs: dict[str, Any]) -> None:
        perms = {
            s["id"]: {k: v for k, v in s["with"].items() if k.startswith("permission-")}
            for s in _mint_steps(jobs["publish"])
        }
        assert perms == {
            "push-token": {"permission-contents": "write"},
            "release-token": {
                "permission-contents": "write",
                "permission-issues": "write",
                "permission-pull-requests": "write",
            },
        }

    def test_app_tokens_reach_only_the_writing_steps(self, jobs: dict[str, Any]) -> None:
        steps = jobs["publish"]["steps"]
        assert {s["name"]: s["env"] for s in steps if _uses_app_token(s)} == {
            "Bump version, push, wait for CI": {
                "VERSION": "${{ needs.gate.outputs.version }}",
                "APP_SLUG": "${{ steps.push-token.outputs.app-slug }}",
                "PUSH_TOKEN": "${{ steps.push-token.outputs.token }}",
                "GH_TOKEN": "${{ github.token }}",
            },
            "Create release": {
                "VERSION": "${{ needs.gate.outputs.version }}",
                "GH_TOKEN": "${{ steps.release-token.outputs.token }}",
            },
            "Post replies": {
                "GH_TOKEN": "${{ steps.release-token.outputs.token }}",
                "TAG": "${{ needs.gate.outputs.tag }}",
            },
        }
        names = [s.get("name") or s.get("id") for s in steps]
        # The release token is minted after the CI wait, so the one-hour expiry never bites.
        assert names.index("Bump version, push, wait for CI") < names.index("Mint release token")

    def test_publish_needs_draft_and_vets_it_before_the_bump(self, jobs: dict[str, Any]) -> None:
        publish = jobs["publish"]
        assert set(publish["needs"]) == {"gate", "draft"}
        names = [s.get("name", "") for s in publish["steps"]]
        assert names.index("Check drafts") < names.index("Bump version, push, wait for CI")
        downloads = [s for s in publish["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact")]
        assert [d["with"]["name"] for d in downloads] == ["release-drafts"]

    def test_draft_uploads_only_the_two_drafts(self, jobs: dict[str, Any]) -> None:
        uploads = [s for s in jobs["draft"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        assert len(uploads) == 1
        assert uploads[0]["with"]["path"].splitlines() == [
            "${{ runner.temp }}/out/notes.md",
            "${{ runner.temp }}/out/replies.json",
        ]

    def test_claude_steps_hardened(self, jobs: dict[str, Any]) -> None:
        for step in _claude_steps(jobs["draft"]):
            args = step["with"]["claude_args"]
            assert "--setting-sources user" in args
            assert step["with"]["github_token"] == "${{ secrets.GITHUB_TOKEN }}"
            tools = re.search(r'--allowedTools "([^"]*)"', args)
            assert tools is not None
            assert tools.group(1).split(",") == ALLOWED_TOOLS
            denied = re.search(r'--disallowedTools "([^"]*)"', args)
            assert denied is not None
            assert {"Bash", "WebFetch", "WebSearch"} <= set(denied.group(1).split(","))
            assert "--add-dir ${{ runner.temp }}/out" in args

    def test_actions_pinned_to_sha(self, jobs: dict[str, Any]) -> None:
        for job in jobs.values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]

    def test_checkouts_do_not_persist_credentials(self, jobs: dict[str, Any]) -> None:
        for job in jobs.values():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout"):
                    assert step["with"]["persist-credentials"] is False

    def test_permissions_read_only(self, jobs: dict[str, Any]) -> None:
        for job in jobs.values():
            assert set(job["permissions"].values()) == {"read"}
        assert jobs["draft"]["permissions"] == {"contents": "read"}
        assert jobs["publish"]["permissions"] == {"contents": "read", "checks": "read"}

    def test_dry_run_never_publishes(self, jobs: dict[str, Any]) -> None:
        assert "inputs.dry_run" in jobs["publish"]["if"] and "!(" in jobs["publish"]["if"]
        assert "inputs.dry_run" in jobs["preview"]["if"]


class TestAllowedTargets:
    def test_merge_subject_counted(self) -> None:
        subjects = ["Merge PR #238: add thing (#237)", "fix: H6022 (#240)"]
        assert rr.allowed_targets(subjects, "") == {237, 238, 240}

    def test_unrelated_number_rejected(self) -> None:
        assert 999 not in rr.allowed_targets(["fix (#1)"], "")

    def test_queue_entries_counted(self) -> None:
        text = "header ## #5 not counted\n<!-- entries below -->\n## #77\nclose: no\nbrief: hi\n"
        assert rr.allowed_targets([], text) == {77}


class TestParseQueue:
    def test_all_close_values(self) -> None:
        text = "<!-- entries below; x -->\n"
        for n, value in enumerate(("yes", "no", "duplicate", "declined"), start=1):
            text += f"## #{n}\nclose: {value}\nbrief: b{n}\n\n"
        entries = rr.parse_queue(text)
        assert [(e.number, e.close, e.brief) for e in entries] == [
            (1, "yes", "b1"),
            (2, "no", "b2"),
            (3, "duplicate", "b3"),
            (4, "declined", "b4"),
        ]

    def test_unknown_close_is_no(self) -> None:
        entries = rr.parse_queue("<!-- entries below -->\n## #3\nclose: nuke\nbrief: x\n")
        assert entries[0].close == "no"


class TestSanitizeAndReject:
    def test_sanitize(self) -> None:
        assert rr.sanitize("a<!-- hidden\ninstr -->b​c‮d") == "abcd"

    def test_foreign_url_rejected(self) -> None:
        assert rr.reject_reason("see https://evil.example/x") is not None

    def test_lookalike_host_rejected(self) -> None:
        assert rr.reject_reason("https://github.com/lasswellt/govee-homeassistant.evil/x") is not None
        assert rr.reject_reason("https://github.com.evil.io/lasswellt/govee-homeassistant") is not None

    def test_repo_and_ha_urls_ok(self) -> None:
        assert rr.reject_reason("https://github.com/lasswellt/govee-homeassistant/issues/5") is None
        assert rr.reject_reason("[x](https://my.home-assistant.io/redirect/integrations/)") is None

    def test_bash_fence_rejected(self) -> None:
        assert rr.reject_reason("run:\n```bash\ncurl x\n```") is not None

    def test_prompt_line_rejected(self) -> None:
        assert rr.reject_reason("```\n$ rm -rf x\n```") is not None

    def test_plain_text_ok(self) -> None:
        assert rr.reject_reason("Fixed in **v2026.10.1**.") is None

    @pytest.mark.parametrize(
        "secret",
        [
            "sk-ant-oat01-abcdefghijklmnop",
            "ghp_" + "a1B2" * 9,
            "ghs_" + "Z9y8" * 9,
            "gho_" + "Q7w6" * 9,
            "github_pat_11ABCDEFG",
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu1SU1LfVLPHCozMxH2Mo",
        ],
    )
    def test_credentials_rejected(self, secret: str) -> None:
        assert rr.reject_reason(f"token: {secret}") is not None

    def test_long_plain_identifiers_and_urls_ok(self) -> None:
        assert rr.reject_reason("custom_components_govee_coordinator_async_control_device_helper") is None
        assert (
            rr.reject_reason("https://github.com/lasswellt/govee-homeassistant/blob/main/custom_components/x.py")
            is None
        )
        assert rr.reject_reason("commit 0123456789abcdef0123456789abcdef01234567") is None


class TestPlanner:
    def test_dry_run_calls_nothing(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("subprocess.run called in dry-run")

        monkeypatch.setattr(subprocess, "run", boom)
        queue = "<!-- entries below -->\n## #50\nclose: duplicate\nbrief: dup\n"
        replies = [
            {"number": 237, "body": "Fixed in v1."},
            {"number": 50, "body": "Duplicate of another."},
            {"number": 999, "body": "nope"},
            {"number": 237, "body": "see https://evil.example"},
        ]
        new_text, log = rr.run_post(replies, queue, ["Merge PR #238 (#237)"], dry_run=True)
        out = capsys.readouterr().out
        assert "would comment on #237" in out
        assert "would close #50 (close: duplicate)" in out
        assert any(line.startswith("skip #999") for line in log)
        assert "evil" not in out
        assert "## #50" not in new_text

    def test_close_only_from_queue(self) -> None:
        entries = rr.parse_queue("<!-- entries below -->\n## #8\nclose: yes\nbrief: b\n")
        actions = rr.plan_actions(
            [{"number": 8, "body": "ok"}, {"number": 9, "body": "ok"}, "junk", {"number": True, "body": "x"}],
            entries,
            {8, 9},
        )
        assert [(a.number, a.close, a.skip_reason) for a in actions[:2]] == [(8, "yes", None), (9, "no", None)]
        assert all(a.skip_reason == "malformed reply item" for a in actions[2:])

    def test_real_post_comments_and_closes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 1 if args[:3] == ["gh", "pr", "view"] else 0, "", "")

        monkeypatch.setattr(rr, "_run", fake)
        queue = "<!-- entries below -->\n## #50\nclose: declined\nbrief: no\n## #51\nclose: no\nbrief: keep\n"
        replies = [{"number": 50, "body": "Thanks."}]
        new_text, _ = rr.run_post(replies, queue, [], dry_run=False)
        assert ["gh", "issue", "close", "50", "--reason", "not planned"] in calls
        assert "## #50" not in new_text and "## #51" in new_text

    def test_duplicate_draft_skipped(self) -> None:
        actions = rr.plan_actions([{"number": 1, "body": "a"}, {"number": 1, "body": "b"}], [], {1})
        assert [a.skip_reason for a in actions] == [None, "duplicate draft"]

    def test_long_body_skipped_not_truncated(self) -> None:
        actions = rr.plan_actions([{"number": 1, "body": "x" * (rr.MAX_BODY + 1)}], [], {1})
        assert actions[0].skip_reason is not None and actions[0].body == ""
        assert rr.plan_actions([{"number": 1, "body": "x" * rr.MAX_BODY}], [], {1})[0].skip_reason is None


class TestCloseThread:
    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch, is_pr: bool) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(
                args,
                0 if is_pr and args[:3] == ["gh", "pr", "view"] else 1 if args[:3] == ["gh", "pr", "view"] else 0,
                "",
                "",
            )

        monkeypatch.setattr(rr, "_run", fake)
        return calls

    @pytest.mark.parametrize("close", ["yes", "duplicate", "declined"])
    def test_pr_closed_with_pr_close(self, monkeypatch: pytest.MonkeyPatch, close: str) -> None:
        calls = self._capture(monkeypatch, True)
        assert rr._close_thread(7, close)
        assert calls == [["gh", "pr", "view", "7"], ["gh", "pr", "close", "7"]]

    @pytest.mark.parametrize(
        ("close", "reason"), [("yes", "completed"), ("duplicate", "not planned"), ("declined", "not planned")]
    )
    def test_issue_close_reason(self, monkeypatch: pytest.MonkeyPatch, close: str, reason: str) -> None:
        calls = self._capture(monkeypatch, False)
        assert rr._close_thread(7, close)
        assert calls[-1] == ["gh", "issue", "close", "7", "--reason", reason]


class TestDrafts:
    @staticmethod
    def _write(tmp_path: Path, notes: str = "## Fixes\n- **x** (#1)\n", replies: str = "[]") -> Path:
        (tmp_path / "notes.md").write_text(notes, encoding="utf-8")
        (tmp_path / "replies.json").write_text(replies, encoding="utf-8")
        return tmp_path

    def test_clean_drafts_pass(self, tmp_path: Path) -> None:
        assert rr.check_drafts(self._write(tmp_path, replies='[{"number": 3, "body": "hi"}]')) is None

    def test_extra_file_rejected(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        (tmp_path / "checks.sh").write_text("x", encoding="utf-8")
        assert rr.check_drafts(tmp_path) is not None

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").symlink_to("/etc/hostname")
        (tmp_path / "replies.json").write_text("[]", encoding="utf-8")
        assert rr.check_drafts(tmp_path) is not None

    def test_empty_or_secret_notes_rejected(self, tmp_path: Path) -> None:
        assert rr.check_drafts(self._write(tmp_path, notes="  \n")) is not None
        assert rr.check_drafts(self._write(tmp_path, notes="sk-ant-oat01-leak")) is not None

    @pytest.mark.parametrize(
        "replies",
        [
            "{}",
            "not json",
            '[{"number": 1}]',
            '[{"number": 1, "body": "x", "close": "yes"}]',
            '[{"number": 0, "body": "x"}]',
            '[{"number": true, "body": "x"}]',
            '[{"number": 1, "body": 5}]',
            "[" + ",".join(['{"number": 1, "body": "x"}'] * (rr.MAX_REPLIES + 1)) + "]",
        ],
    )
    def test_schema_violations_rejected(self, tmp_path: Path, replies: str) -> None:
        assert rr.check_drafts(self._write(tmp_path, replies=replies)) is not None

    def test_targets_command(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        subjects = tmp_path / "s.txt"
        subjects.write_text("Merge PR #238: x (#237)\nfix: y\n", encoding="utf-8")
        queue = tmp_path / "q.md"
        queue.write_text("<!-- entries below -->\n## #5\nclose: no\nbrief: b\n", encoding="utf-8")
        assert rr.main(["targets", "--subjects-file", str(subjects), "--queue", str(queue)]) == 0
        assert capsys.readouterr().out.split() == ["5", "237", "238"]
