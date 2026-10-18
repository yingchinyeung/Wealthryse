# Release data report: what numbers change

Before an image that touches silver or gold goes live, its jobs run on a restored copy of
the database, and a before-and-after report of every silver and gold table is made. Its
public summary goes on the PR; the full report, with the figures, goes to the approver
through the project's private folder. The approver approves the figures as well as the
code. This is upgrade U6. On 18 Oct a
benchmark change (bank deposits counted as external flows) rewrote
`gold.performance_comparison_daily` with no such report.

`scripts/release_data_report.py` makes the report. It is a tool people run by hand; nothing
runs it in production.

## When

- Every PR with a migration, or with code that writes a silver or gold table: the loader,
  derived portfolios, benchmark comparison, risk, crisis scenarios, month-end snapshots.
  If in doubt, run it: a report that says nothing changed is useful too.
- Before the image goes live, so the report is on the PR when it is approved.

## Steps

From the admin VM, in a clone of the PR's branch (the tool reads the table list from
`infra/medallion/catalog.json` in the clone):

1. **Restore a copy** of the v2 server, as the 17 Oct rehearsal did: a point-in-time
   restore into the spare upgrade-recovery subnet. Restore from a moment after the daily
   cycle has finished (after the crisis job that starts at 09:20 UTC), so the copy already
   holds the current image's figures for the day. Nothing live is touched.
2. **Snapshot before** (read-only):

   ```
   export DATABASE_ADMIN_URL=<the copy's admin URL, never the live server's>
   python scripts/release_data_report.py snapshot --out before.jsonl.gz
   ```

3. **Run the candidate on the copy:** apply the PR's migration, if it has one, with
   `bootstrap_postgres.py --only-migration <file>`, then one execution of each job the PR
   changes, on the candidate image, with its database URL pointing at the copy, in the
   daily order (benchmark, sectors, derived, risk, crisis). On the copy, no job may ask IBKR for a
   statement (institutional-daily stays off) or write to live storage: leave
   `MARKET_DATA_RAW_CONTAINER` and the report containers unset.
4. **Snapshot after:**

   ```
   python scripts/release_data_report.py snapshot --out after.jsonl.gz
   ```

5. **Compare:**

   ```
   python scripts/release_data_report.py compare --before before.jsonl.gz --after after.jsonl.gz
   ```

   It writes the full report, `release-data-report.md` and `release-data-report.json`, and
   the public summary, `release-data-summary.md` (`--public-summary` for another name), and
   prints the public summary. It exits 0 when nothing changed, 1 when figures changed
   (expected for a release that changes figures), and 2 when it could not run.
   `summary --report release-data-report.json --out release-data-summary.md` writes the
   public summary again from a saved full report.
6. **Post only the public summary on the PR:**
   `gh pr comment <PR number> --body-file release-data-summary.md`. The repository is
   public, and the full report's deltas can reveal client figures (a change from zero
   shows the whole value), so the full report never goes on a PR, an issue or a CI log.
7. **Give the approver the full report:** copy `release-data-report.md` and
   `release-data-report.json` to the project's private shared folder, under
   `/mnt/project-files/release-reports/<PR number>/`.
8. **Delete** the copy, `before.jsonl.gz` and `after.jsonl.gz`.

With two copies restored from the same moment (one with each image's jobs run on it),
compare them directly: `compare --before env:BEFORE_DATABASE_URL --after env:AFTER_DATABASE_URL`,
with each variable holding that copy's URL.

## Reading the report

The **public summary** has counts and names only: how many tables changed, were unchanged
or were not compared; for each changed table, rows added, removed and changed, whether
any number changed beyond the tolerance (and in which columns), which status and version
columns changed, and which other columns changed; the frozen month-end snapshot check
(pass or fail); and the attention items. It has no figures, deltas, dates or subject
labels.

The **full report** (private):

- **The first lines** say how many tables changed and whether the frozen month-end
  snapshots are unchanged. **Needs attention** lists what stops a release: a frozen or
  approved month-end snapshot that changed (they never change; the check is the one
  `medallion_layers.py` makes around each S3 batch), a table that disappeared, or before
  and after read with different row-level security.
- **Biggest changes** has one line per changed table: rows before and after; rows added,
  removed and changed (matched by the table's primary key); the dates touched; and the
  column with the most changed values, with its largest change (Δ, after minus before)
  and largest relative change (Δ over |before|), each with where it happened.
- **Details by table** (folded) has every changed column, including the numbers inside
  jsonb payloads (risk and crisis results) by path, value changes of statuses and
  versions, and the same counts per account or subject.
- Rows added for a new day are normal on a copy. Changes to past dates are what the PR
  must explain, figure by figure, before it is approved.

## What it compares, and what it shows

- Every silver and gold table in the catalog. Views are not compared: they read the
  tables. Columns that every rebuild rewrites are skipped: `generated_at`, `fetched_at`,
  `checked_at`, `first_seen_at`, `last_seen_at`, `last_seen_run_id`, `duration_ms`,
  `timings_json`. Numbers equal within a relative 1e-9 count as unchanged (`--rel-tol`).
- Numeric columns, the IBKR `*_text` columns (numbers kept as text) and the numbers inside
  jsonb columns are compared as numbers. Other text is compared but never shown, apart
  from short status and version values such as `quality_status` and `calculation_version`.
- A raised `calculation_version` or `analytics_version` is part of the key, so the new
  version's rows show as added, and the version's row counts appear in the details.
- **The full report** holds aggregates only. Accounts appear as `u…#1`, `u…#2`, other
  subjects as `s…#1`; the labels hold within one report. There are no account numbers,
  names or text values, and any IBKR account id that reached the text is masked as `u…`.
  Its deltas and relative changes are still client figures, so it stays private.
- **The public summary** holds counts and table, column and migration names only.
- **Snapshot files** hold figures row by row, with keys, subjects and text replaced by
  sha256 digests. They are still client data: they are created readable by their owner
  only, stay on the admin VM, and are deleted with the copy.
- The database URL comes from an environment variable (`--url-env`, default
  `DATABASE_ADMIN_URL`) and is never printed. The connection is read-only, in one
  `REPEATABLE READ` transaction. A login that does not bypass row-level security reads the
  default tenant's rows (`--tenant` for another).
- `--table <layer.name>` (repeatable) limits a snapshot or comparison to some tables.
