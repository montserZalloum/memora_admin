Run the GitNexus reindex workflow: analyze the impact of the current uncommitted changes, push them, then rebuild the knowledge graph with embeddings.

Optional argument `$ARGUMENTS` is used as the commit message. If empty, write a concise message summarizing the staged diff.

Follow these steps exactly, in order. Run all commands from the repo root (`/home/corex/aurevia-bench/apps/memora_admin`).

1. **Impact analysis (before committing — the diff must still be unstaged).** Map the current changes to affected symbols and execution flows:
   ```bash
   npx -y gitnexus detect-changes --scope unstaged
   ```
   Report what symbols/flows are affected. If the output shows a high-risk blast radius (many upstream dependants), call it out before continuing — but proceed unless the user has told you to stop.

   If there are no uncommitted changes, stop here and report "nothing to reindex" — do not create an empty commit.

2. **Commit and push.** Stage everything, commit, and push to the current branch's upstream:
   ```bash
   git add -A
   git commit -m "<message>"
   git push
   ```
   - Use `$ARGUMENTS` as the commit message if provided; otherwise summarize the diff in one concise line.
   - End the commit message with the trailer:
     ```
     Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
     ```
   - If the current branch has no upstream, push with `git push -u origin HEAD`.
   - If the push is rejected (remote ahead), stop and report — do not force-push.

3. **Reindex with embeddings.** Rebuild the GitNexus knowledge graph including semantic embeddings for the now-pushed commit:
   ```bash
   npx -y gitnexus analyze --embeddings
   ```
   This is the slow step (embeddings run a local ML model). Run it in the background and wait for it to finish.

4. **Report** the final node/edge/cluster/flow counts from the analyze output and confirm the index is up-to-date:
   ```bash
   npx -y gitnexus status
   ```
