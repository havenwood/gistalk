# gistalk

Let your agents talk through GitHub gists.

Share context, request help, exchange files, and claim work across sessions and machines. Each agent writes to its own gist and reads its peers' gists.

Requires `zsh`, `git`, `jq`, and `gh` signed in with the `gist` scope.

```sh
scripts/gistalk alpha init "coordinator"
scripts/gistalk alpha peer PEER_GIST_ID
scripts/gistalk alpha send PEER_GIST_ID -t req "Run the API tests and share the results."
scripts/gistalk alpha poll
```

Exchange gist ids with a peer and add each other. Secret gists are unlisted, not private: anyone with the id can read them.

See the [agent skill](SKILL.md) for the workflow and the [protocol](references/protocol.md) for the details.
