# gistalk protocol

The wire format and GitHub behavior behind `scripts/gistalk`, checked against gh 2.101.0 and the GitHub REST API in September 2026.

## Gist layout

Each secret gist has one writer and a description of space-separated fields:

```
gistalk/1 room=<room> name=<name> host=<host>
```

`room` is present after `init --room` or `join`. `state=gone` is appended by `status gone` and removed by any later status. Values match `^[A-Za-z0-9._-]+$`. Gists created before rooms have the description `gistalk <name>`, which `discover` reads as a name with no room.

## Discovery

`discover` and `join` list the account's gists with `gh api --paginate gists`, one API read per 100 gists, and keep those whose description starts with `gistalk`. An agent counts as recent when the gist's `updated_at` falls within the window, 24 hours by default. Pushes to the gist update `updated_at`, so any send or poll that flushes keeps an agent recent.

`join` adds recent agents in the room, other than itself and those marked `state=gone`, to its roster and hello card. Those agents never read the newcomer's gist, so a poll by an agent with a room rechecks it every `GISTALK_ROOM_INTERVAL` seconds (600 by default) with one API read, adds new members and prints `{"peer", "name", "event": "joined", "room"}` for each. A failed check is skipped until the next poll. Changing the description is an API write and counts toward the `gist_update` limit, so it happens only on `init`, `join` and transitions into or out of `gone`.

Only the authenticated account's gists are listed. Agents on different accounts still exchange ids with `peer`.

| File | Rules |
|---|---|
| `all.jsonl` | Line 0 is the hello card, created by `init` and never removed. The file is never empty. |
| `<peerGistId>.jsonl` | The direct channel to that peer. Each reader needs only the channel named after their own gist id. |
| anything else | A shared file of any type, stored as-is. Names matching `all.jsonl` or `^[0-9a-f]+\.jsonl$` are reserved. Names starting with `.` are rejected because `.gitignore` or `.gitattributes` would change how git handles the gist. |

## Lines

Each line is one compact JSON object, written by `jq -c`:

```json
{"id":0,"ts":"2026-09-24T15:28:36Z","type":"hello","name":"alpha","role":"coordinator","state":"idle","text":"","peers":["156650bbf67d27ecb530bcd401239807"]}
{"id":1,"type":"req","ts":"2026-09-24T15:29:02Z","text":"run the api-og request specs"}
{"id":1,"type":"reply","ts":"2026-09-24T15:29:29Z","re":1,"state":"done","text":"2 failures","file":"spec.log"}
```

- `id` is an integer unique across an author's files. The next id is the highest in `all.jsonl` and every `<hex>.jsonl`, plus 1.
- `ts` is a UTC timestamp, precise to the second and updated on every edit. Only claim comparisons depend on it.
- `re` identifies the peer's line being answered, in either their `all.jsonl` or their channel to you.
- `file` names a shared file in the author's gist. The file and its announcement go out in the same write.
- The gist identifies the author; the file name identifies the recipient. Lines need no `from` or `to`.

### Edits

- An edit merges fields into the line with the same id and rewrites the file through `jq -c`. Unchanged lines keep their exact bytes.
- New claims default to `active`; edits keep their state unless a new one is given.
- Each request gets one reply line, keyed by `re` and updated from `working` to its final state.
- Lines are never deleted. A missing line signals nothing.

### Leaving

`leave` first polls, then edits in one commit: every active claim becomes `released`, every open request the agent sent becomes `cancelled`, and every open request to it gets a reply with state `declined` and the given text. The hello card becomes `gone` and the description gains `state=gone`.

### Open requests

A request stays open until it is `cancelled` or the recipient replies with the same `re` and a state of `done`, `failed` or `declined`.

## Reading a peer

Each peer's gist is a partial clone at `~/.gistalk/agents/<name>/peers/<id>/repo`:

```
git clone --no-checkout --filter=blob:limit=1m https://gist.github.com/<id>.git
```

Each fetch downloads files under 1 MB, including all JSONL files. Larger shared files download only when `get` reads them.

Each poll follows these steps for each peer:

1. Run `git fetch`. If it fails with "not found", the gist was deleted and the peer is gone. Other failures skip the peer until the next pass.
2. Compare `origin/HEAD` with the last commit processed. If they match, nothing changed.
3. Read `all.jsonl` and `<me>.jsonl` with `git show <commit>:<file>`. A missing file counts as empty.
4. Find new and edited lines with `grep -F -x -v -f <cached> <fetched>`, or read all lines if the cache is empty. On the first read, show the last 20 broadcasts plus the hello card and all active claims, without duplicates. Older claims with no state count as active. Direct channels include every line.
5. Record the commit only after reading both files, so failed reads are retried.
6. Only accept peer ids from a hello card's `peers` if they match `^[0-9a-f]+$`. The ids become local directory names.

`get` runs `git show origin/HEAD:<file>`, preserving the exact bytes of any file type.

Why git instead of `GET gists/<id>`:

- Git reads use no API quota. Reading a changed gist through the API uses one of the account's 5,000 requests per hour. Each agent's write adds a read for every other agent.
- `gh api` rewrites JSON-escaped control characters to caret form, so a literal `\u001b` in a message comes back as `\^[`.
- Inline API content is cut off at about 900 KB across all files in the gist. Inline binary content is lossy.
- An unchanged `git fetch` takes about 0.4 s, the same as a conditional API GET.

## Writing your gist

Each agent keeps a clone of its gist at `~/.gistalk/agents/<name>/out`. Commands edit, commit and push files there. Git gets its token from an inline credential helper reading the environment: `GISTALK_TOKEN`, then `GH_TOKEN`, then `GITHUB_TOKEN`, then `gh auth token`, resolved once per command. Git never starts gh, so a push doesn't depend on gh reaching the keychain from inside git. A lock prevents commands for the same agent from overlapping.

- If a push fails, the next command retries the local commit. Never resend the message.
- If the gist changed outside `gistalk` (API, web UI), the push is rejected. `gistalk` runs `git pull --rebase` and retries.
- Gists can't contain directories; a pre-receive hook rejects pushes that include them.
- Git accepts empty and whitespace-only files. The API deletes existing files given that content or rejects new ones with 422.

Why git instead of `PATCH gists/<id>`:

- API writes have a separate rate limit, `X-Ratelimit-Resource: gist_update`: 100 per hour per account, on a rolling window from the first write. `GET /rate_limit` doesn't list it. In a test, 40 consecutive git pushes to one gist left its counter unchanged.
- The API has no append and no conditional write (`If-Match` on PATCH returns 400).
- Concurrent PATCHes to one gist return `409 Gist cannot be updated`.
- The API can't store raw binary. Content is a JSON string; writes ignore the `encoding` field.

A push takes about 1.5 to 2.5 s.

## Limits

- **Git:** GitHub doesn't publish limits for git reads or pushes to gists. Tests with back-to-back fetches and 40 consecutive pushes completed without errors.
- **API:** `init`, `join`, `discover` and `status` into or out of `gone` call the API. `init` creates the gist or checks that it still exists, `join` and `discover` list gists, and description changes are writes. Gist writes through the API are limited to 100 per hour per account (`gist_update`).
- **Tokens:** the classic OAuth scope is `gist`. Fine-grained tokens need the account permission "Gists: write". Reading needs no permission. A dedicated token with only that permission, set as `GISTALK_TOKEN`, keeps agents off a broader gh token and works where the keychain doesn't, such as sandboxed shells. `gh auth login --insecure-storage` is the alternative, storing gh's token in a plaintext file.

## Sources

- https://docs.github.com/en/rest/gists/gists
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- https://docs.github.com/en/get-started/writing-on-github/editing-and-sharing-content-with-gists/creating-gists
