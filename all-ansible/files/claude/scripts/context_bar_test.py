#!/usr/bin/env python3
"""Regression tests for context-bar.sh, driven through its stdin/stdout.

Run with: python3 ~/.claude/scripts/context_bar_test.py
"""

import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "context-bar.sh"


class ModelAndEffortTest(unittest.TestCase):
    def test_leads_with_model_name(self):
        self.assertTrue(render().startswith("Opus | "))

    def test_known_effort_levels_abbreviate_onto_model(self):
        for level, char in [("low", "l"), ("medium", "m"), ("high", "h"), ("xhigh", "x"), ("max", "!")]:
            with self.subTest(level=level):
                self.assertTrue(render(effort=level).startswith(f"Opus{char} | "))

    def test_unknown_effort_level_passes_through(self):
        self.assertTrue(render(effort="turbo").startswith("Opusturbo | "))

    def test_falls_back_to_model_id(self):
        self.assertTrue(render(model={"id": "claude-opus-5-5"}).startswith("claude-opus-5-5 | "))


class DirectoryAndGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])

    def test_outside_git_shows_dir_only(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(segments(render(cwd=plain))[:2], ["Opus", "plain"])
        self.assertEqual(len(segments(render(cwd=plain))), 3)

    def test_repo_root_shows_branch(self):
        repo = make_repo(self.tmp / "widgets")
        self.assertEqual(segments(render(cwd=repo))[1:3], ["widgets", "main"])

    def test_dirty_tree_marks_branch(self):
        repo = make_repo(self.tmp / "widgets")
        (repo / "new.txt").write_text("x")
        self.assertEqual(segments(render(cwd=repo))[2], "main*")

    def test_subdir_prefixes_repo_name(self):
        repo = make_repo(self.tmp / "widgets")
        (repo / "lib").mkdir()
        (repo / "lib" / ".keep").write_text("")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "lib")
        self.assertEqual(segments(render(cwd=repo / "lib"))[1:3], ["lib", "(widgets) main"])

    def test_detached_head_shows_short_sha(self):
        repo = make_repo(self.tmp / "widgets")
        sha = git(repo, "rev-parse", "--short", "HEAD")
        git(repo, "checkout", "-q", "--detach")
        self.assertEqual(segments(render(cwd=repo))[2], sha)

    def test_worktree_root_marks_worktree(self):
        repo = make_repo(self.tmp / "widgets")
        wt = self.tmp / "widgets-feature"
        git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
        self.assertEqual(segments(render(cwd=wt))[1:3], ["widgets-feature", "(widgets) feature ⧉"])

    def test_worktree_subdir_names_the_worktree(self):
        repo = make_repo(self.tmp / "widgets")
        (repo / "lib").mkdir()
        (repo / "lib" / ".keep").write_text("")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "lib")
        wt = self.tmp / "widgets-feature"
        git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
        self.assertEqual(segments(render(cwd=wt / "lib"))[2], "(widgets) feature ⧉ widgets-feature")


class ContextBarTest(unittest.TestCase):
    def test_bar_fills_in_tenths_with_half_steps(self):
        cases = {
            0: "░░░░░░░░░░",
            4: "▄░░░░░░░░░",
            55: "█████▄░░░░",
            58: "██████░░░░",
            100: "██████████",
        }
        for pct, bar in cases.items():
            with self.subTest(pct=pct):
                self.assertEqual(segments(render(pct=pct))[-1], f"{bar} {pct}% of 1M")

    def test_whole_millions_read_as_m(self):
        self.assertIn("4% of 1M", render(context_size=1_000_000))

    def test_smaller_windows_read_as_k(self):
        self.assertIn("4% of 200k", render(context_size=200_000))

    def test_no_tokens_suffix(self):
        self.assertNotIn("tokens", render())


class CacheExpiryTest(unittest.TestCase):
    def test_reads_as_warm_while_over_thirty_minutes_left(self):
        self.assertEqual(cache_label([assistant(ago=120, ttl="1h")]), "warm")

    def test_shows_minutes_left_once_under_thirty(self):
        self.assertEqual(cache_label([assistant(ago=30 * 60 + 30, ttl="1h")]), "exp 29m")
        self.assertEqual(cache_label([assistant(ago=50 * 60 + 30, ttl="1h")]), "exp 9m")

    def test_rounds_minutes_down(self):
        # 5m TTL written 3m20s ago leaves 1m40s
        self.assertEqual(cache_label([assistant(ago=200, ttl="5m")]), "exp 1m")

    def test_under_a_minute_left_reads_as_uncached(self):
        self.assertEqual(cache_label([assistant(ago=270, ttl="5m")]), "uncached")

    def test_expired_reads_as_uncached(self):
        self.assertEqual(cache_label([assistant(ago=600, ttl="5m")]), "uncached")

    def test_cache_reads_keep_the_ttl_of_the_last_write(self):
        # Under a 5m TTL the recent read would leave 3m; under 1h it leaves 58m
        self.assertEqual(cache_label([assistant(ago=50 * 60, ttl="1h"), assistant(ago=120, ttl=None)]), "warm")

    def test_ignores_entries_that_never_touched_the_cache(self):
        self.assertEqual(cache_label([
            assistant(ago=600, ttl="5m"),
            {"type": "assistant", "timestamp": iso(ago=10), "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
            {"type": "user", "timestamp": iso(ago=5), "message": {"content": "hi"}},
        ]), "uncached")

    def test_no_cache_segment_without_a_transcript(self):
        self.assertEqual(cache_label(None), "")

    def test_hidden_until_context_passes_100k_tokens(self):
        expired = [assistant(ago=600, ttl="5m")]
        self.assertEqual(cache_label(expired, tokens=100_000), "")
        self.assertEqual(cache_label(expired, tokens=100_001), "uncached")

    def test_no_cache_segment_without_api_calls(self):
        self.assertEqual(cache_label([{"type": "user", "timestamp": iso(ago=5), "message": {"content": "hi"}}]), "")


def render(cwd=None, model=None, effort=None, pct=4, context_size=1_000_000, tokens=40_000, transcript=None):
    """Run the statusline against fake session input, returning it without colours."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "model": model or {"display_name": "Opus"},
            "cwd": str(cwd or tmp),
            "context_window": {
                "used_percentage": pct, "context_window_size": context_size, "total_input_tokens": tokens,
            },
        }
        if effort:
            payload["effort"] = {"level": effort}
        if transcript is not None:
            path = Path(tmp) / "transcript.jsonl"
            path.write_text("".join(json.dumps(entry) + "\n" for entry in transcript))
            payload["transcript_path"] = str(path)
        result = subprocess.run(
            [str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, check=True,
        )
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).strip()


def segments(line):
    return line.split(" | ")


def cache_label(transcript, tokens=250_000):
    """Whatever the statusline appends after the context size, for the given transcript."""
    return render(tokens=tokens, transcript=transcript).split("% of 1M", 1)[1].strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("hi")
    git(path, "add", ".")
    git(path, "commit", "-qm", "init")
    return path


def git(cwd, *args):
    """Run git isolated from the user's own config, so signing or hooks can't interfere."""
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def assistant(ago, ttl):
    """An API response from `ago` seconds back; ttl is the cache bucket it wrote, or None for a pure read."""
    written = 2000 if ttl else 0
    return {
        "type": "assistant",
        "timestamp": iso(ago),
        "message": {
            "usage": {
                "input_tokens": 2,
                "output_tokens": 300,
                "cache_read_input_tokens": 50_000,
                "cache_creation_input_tokens": written,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": written if ttl == "1h" else 0,
                    "ephemeral_5m_input_tokens": written if ttl == "5m" else 0,
                },
            },
        },
    }


def iso(ago):
    moment = datetime.now(timezone.utc) - timedelta(seconds=ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


if __name__ == "__main__":
    unittest.main()
