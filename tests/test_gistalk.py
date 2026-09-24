"""Run offline: python3 -B -m unittest discover -s tests -v."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class GistalkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gistalk-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1] / "scripts/gistalk"
        self.script = self.root / "gistalk"
        self.script.write_text(source.read_text().replace(
            "D=$HOME/.gistalk/agents/$1 cmd=$2",
            f"D={self.root}/agents/$1 cmd=$2",
        ))
        for name, ident in [("alpha", "aaa"), ("beta", "bbb")]:
            directory = self.agent(name)
            directory.mkdir(parents=True)
            (directory / "me").write_text(ident + "\n")
            (directory / "roster").write_text("")
            self.git("init", "--bare", self.root / ident)
            self.git("clone", self.root / ident, directory / "out")
            self.append(name, "all.jsonl", {
                "id": 0, "type": "hello", "name": name,
                "state": "idle", "peers": [],
            })
            self.publish(name)
        for name, peer in [("alpha", "bbb"), ("beta", "aaa")]:
            directory = self.agent(name)
            (directory / "roster").write_text(peer + "\n")
            self.git("clone", self.root / peer, directory / "peers" / peer / "repo")

    def agent(self, name):
        return self.root / "agents" / name

    def git(self, *args):
        return subprocess.run([
            "git", "-c", "init.defaultBranch=main", "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
            *map(str, args),
        ], check=True, capture_output=True, text=True).stdout

    def publish(self, name):
        directory = self.agent(name) / "out"
        self.git("-C", directory, "add", ".")
        self.git("-C", directory, "commit", "-m", "Seed messages")
        self.git("-C", directory, "push", "-u", "origin", "HEAD")

    def append(self, name, filename, event):
        with (self.agent(name) / "out" / filename).open("a") as file:
            file.write(json.dumps(event, separators=(",", ":")) + "\n")

    def run_cli(self, name, *args):
        return subprocess.run(
            [os.environ.get("GISTALK_TEST_ZSH", "zsh"), str(self.script), name, *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def events(self, name, command):
        return [json.loads(line) for line in self.run_cli(name, command).splitlines()]

    def test_claim_defaults_and_edits(self):
        ident = self.run_cli("alpha", "send", "all", "-t", "claim", "repo:file")
        self.assertEqual(self.events("beta", "poll")[-1]["state"], "active")
        self.run_cli("alpha", "send", "all", "-i", ident, "-s", "released")
        self.run_cli("alpha", "send", "all", "-i", ident, "finished")
        self.assertEqual(self.events("beta", "poll")[0]["state"], "released")
        self.run_cli("alpha", "send", "all", "-t", "claim", "-s", "released", "done")
        self.assertEqual(self.events("beta", "poll")[0]["state"], "released")

    def test_first_poll_keeps_claims_without_replaying_history(self):
        for ident, state in [(1, "active"), (2, None), (3, "released")]:
            claim = {"id": ident, "type": "claim", "text": "repo:file"}
            if state:
                claim["state"] = state
            self.append("alpha", "all.jsonl", claim)
        for ident in range(4, 25):
            self.append("alpha", "all.jsonl", {"id": ident, "type": "msg", "text": "update"})
        self.append("alpha", "all.jsonl", {"id": 25, "type": "claim", "state": "active"})
        self.publish("alpha")
        events = self.events("beta", "poll")
        self.assertEqual([event["id"] for event in events], [0, 1, 2, *range(6, 26)])
        self.assertEqual(self.events("beta", "poll"), [])

    def test_poll_refreshes_requests_and_replies(self):
        ident = self.run_cli("alpha", "send", "bbb", "-t", "req", "Run the tests")
        self.assertEqual(self.events("beta", "pending"), [])
        self.events("beta", "poll")
        self.assertEqual(self.events("beta", "pending")[0]["via"], "waiting-on-me")
        for state in ["working", "done"]:
            self.run_cli("beta", "send", "aaa", "-r", ident, "-s", state, state)
        self.assertEqual(len(self.events("alpha", "pending")), 1)
        replies = self.events("alpha", "poll")
        self.assertEqual([event["state"] for event in replies if event["type"] == "reply"], ["done"])
        self.assertEqual(self.events("alpha", "pending"), [])
        self.assertEqual(self.events("beta", "pending"), [])

    def test_peer_url_and_literal_messages(self):
        self.run_cli("alpha", "peer", "https://gist.github.com/example/bbb")
        message = '-s done: $HOME `whoami` \\u001b [.*] é漢字🙂\nsecond line'
        self.run_cli("alpha", "send", "beta", "--", message)
        events = self.events("beta", "poll")
        messages = [event for event in events if event["type"] == "msg"]
        self.assertEqual([event["text"] for event in messages], [message])
        self.run_cli("alpha", "send", "beta", "another message")
        self.assertEqual([event["text"] for event in self.events("beta", "poll")], ["another message"])


if __name__ == "__main__":
    unittest.main()
