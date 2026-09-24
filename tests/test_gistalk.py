"""Run offline: python3 -B -m unittest discover -s tests -v."""

from datetime import datetime, timedelta, timezone
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
        # A fake gh serving gists.json, so descriptions work offline.
        self.gists = self.root / "gists.json"
        self.gists.write_text("[]")
        self.gh = self.root / "gh"
        self.gh.write_text(FAKE_GH.replace("GISTS", str(self.gists)))
        self.gh.chmod(0o755)

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
            env={**os.environ, "GISTALK_GH": str(self.gh)},
        ).stdout.strip()

    def events(self, name, *command):
        return [json.loads(line) for line in self.run_cli(name, *command).splitlines()]

    def seed_gists(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.gists.write_text(json.dumps([
            {"id": "aaa", "updated_at": fresh, "description": "gistalk alpha"},
            {"id": "bbb", "updated_at": fresh, "description": "gistalk/1 room=shop name=beta host=box"},
            {"id": "ccc", "updated_at": fresh, "description": "gistalk/1 room=other name=gamma host=box"},
            {"id": "ddd", "updated_at": fresh, "description": "gistalk/1 room=shop name=delta host=box state=gone"},
            {"id": "eee", "updated_at": stale, "description": "gistalk/1 room=shop name=eps host=box"},
            {"id": "fff", "updated_at": fresh, "description": "notes"},
        ]))

    def description(self, ident):
        return next(g["description"] for g in json.loads(self.gists.read_text()) if g["id"] == ident)

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

    def test_discover_filters_rooms_departures_and_age(self):
        self.seed_gists()
        self.assertEqual([a["id"] for a in self.events("alpha", "discover")], ["aaa", "bbb", "ccc"])
        shop = self.events("alpha", "discover", "--room", "shop")
        self.assertEqual(shop, [{"id": "bbb", "name": "beta", "room": "shop", "host": "box",
                                 "state": "active", "updated": shop[0]["updated"]}])
        everyone = self.events("alpha", "discover", "--room", "shop", "--all", "--since", "100")
        self.assertEqual({a["id"] for a in everyone}, {"bbb", "ddd", "eee"})
        self.assertEqual(self.events("gamma", "discover", "--room", "other")[0]["name"], "gamma")

    def test_join_peers_recent_room_agents_and_retitles(self):
        self.seed_gists()
        (self.agent("alpha") / "roster").write_text("")
        self.assertEqual(self.run_cli("alpha", "join", "shop"), "bbb")
        self.assertEqual((self.agent("alpha") / "roster").read_text().split(), ["bbb"])
        self.assertTrue(self.description("aaa").startswith("gistalk/1 room=shop name=alpha host="))
        hello = self.events("beta", "poll")[0]
        self.assertEqual(hello["peers"], ["bbb"])

    def test_polls_pick_up_agents_that_joined_later(self):
        self.seed_gists()
        (self.agent("alpha") / "roster").write_text("")
        (self.agent("alpha") / "room").write_text("shop\n")
        joined = [e for e in self.events("alpha", "poll") if e.get("event") == "joined"]
        self.assertEqual(joined, [{"peer": "bbb", "name": "beta", "event": "joined", "room": "shop"}])
        self.assertEqual((self.agent("alpha") / "roster").read_text().split(), ["bbb"])
        self.assertEqual(self.events("alpha", "poll"), [])

    def test_leaving_marks_the_title(self):
        self.seed_gists()
        self.run_cli("alpha", "status", "gone")
        self.assertTrue(self.description("aaa").endswith(" state=gone"))
        self.assertEqual([a["id"] for a in self.events("beta", "discover")], ["bbb", "ccc"])
        self.run_cli("alpha", "status", "idle")
        self.assertNotIn("state=gone", self.description("aaa"))


FAKE_GH = """#!/usr/bin/env python3
import json, subprocess, sys
path = "GISTS"
args = sys.argv[1:]
gists = json.load(open(path))
if args[:2] == ["api", "--paginate"]:
    jq = args[args.index("--jq") + 1]
    out = subprocess.run(["jq", "-c", jq], input=json.dumps(gists), capture_output=True, text=True, check=True)
    sys.stdout.write(out.stdout)
elif args[:3] == ["api", "-X", "PATCH"]:
    ident = args[3].split("/")[1]
    description = args[args.index("-f") + 1].split("=", 1)[1]
    for gist in gists:
        if gist["id"] == ident:
            gist["description"] = description
    json.dump(gists, open(path, "w"))
else:
    sys.exit("fake gh: unsupported " + " ".join(args))
"""


if __name__ == "__main__":
    unittest.main()
