# Releasing the research image

The research image (`ibkr-research`, built from `Dockerfile.research`) runs the jobs
`ibkrv2stg-research` and `ibkrv2stg-research-idle-stop` and nothing else. It is the only
image with scipy and pyarrow. The website, the agents and the daily jobs keep their own
images, and their packages do not change (Medallion condition 8: numpy and scipy go out
through the normal image release, with in-image tests and the drift record).

The image holds:

- `src/ibkr_etl/**/*.py`
- `scripts/research/*.py`
- `requirements-research.lock`
- `SOURCE_MANIFEST.json`, with the sha256 of each file

It holds no templates, no report runtime, no migrations and no private documents. It runs
as user 10001.

## 1. Lock the packages (once, and whenever requirements.lock changes)

`requirements-research.lock` copies the job's entries from `requirements.lock`
(versions, markers and hashes unchanged) and adds scipy 1.16.1 and pyarrow 21.0.0.

**Before the first build, fill in the hashes.** They were left out when the lock was
written, because there was no network then. Run this on a workstation or on the admin VM:

```
python scripts/research/lock_research_requirements.py --fill
python scripts/research/lock_research_requirements.py --check   # exit 0 = ready to build
```

Review the diff. Only the scipy and pyarrow hash lines may change. Then merge it through a
PR like any change.

After a change to `requirements.lock`, run `--write` (offline). `--check` tells you
whether the copied entries are still the same; exit 1 means they have drifted.

## 2. Build on the admin VM

Sign in to `ibkrv2stg-private-admin` through Bastion. Make a clean clone of `main` at the
merge commit, then run `az login` as yourself.

1. Run the dry run. It prints the source tree sha256 and the commands:

   ```
   python3 scripts/research/release_research_image.py build
   ```

2. Build and push:

   ```
   python3 scripts/research/release_research_image.py build --apply
   ```

The build works like this:

- It writes a source-only context to `.work/research-image/<tree sha256>/context`.
- It builds that context with docker, for `linux/amd64`.
- The image's last build step is `scripts/research/check_research_image.py`. The step
  checks:
  - the image runs non-root;
  - numpy, scipy and the Parquet round trip give known answers;
  - the Azure and Postgres packages import;
  - every file matches `SOURCE_MANIFEST.json`.

  If any check fails, the build stops and nothing is pushed.
- It pushes `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-research:main-<commit>`. The output gives
  the digest, `codeVersion` and the two `deploy/deployed-images.json` entries.

If the VM cannot build, `--acr` builds the same context with ACR Tasks instead.

The build refuses in two cases:

- the lock still lacks hashes;
- the clone has uncommitted changes to any file the image ships.

## 3. Deploy the jobs

On Ying's PC (or wherever `az login` has the rights):

```
python scripts/research/switch_on.py deploy --apply --image-digest sha256:<digest> --code-version <commit>
```

This redeploys `infra/research.bicep` with the image. Every resource the file already
created stays as it is.

## 4. Record the image

Add both jobs to `images` in `deploy/deployed-images.json` with the same value, and add a
dated line to `recorded`:

```
"ibkrv2stg-research": "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-research@sha256:<digest>",
"ibkrv2stg-research-idle-stop": "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-research@sha256:<digest>",
```

Merge this through a PR once the jobs run the image. `deploy/drift_check.py` compares every
`ibkrv2stg-*` job with this file.

## Rolling back

Deploy step 3 again with the previous digest from `deploy/deployed-images.json`, then
record it.

## Notes for the integrator

- **Website release:** `deploy/website_release.py` refuses changes to files outside
  `src/ibkr_etl`. `Dockerfile.research` and the research lock are not in its
  `IGNORED_PREFIXES`.
  - Add `'Dockerfile.research'` and a `'requirements-'` prefix to `IGNORED_PREFIXES`.
  - The prefix also covers `requirements-live.lock`, which is listed there already.
  - Do not spell out the research lock's file name in `deploy/`.
    `tests/test_research_isolation.py` reads `research.` followed by a name as a research
    object.
- **Source archive:** `src/ibkr_etl/source_archive.py` does not list the two root files.
  For that reason `release_research_image.py` writes its own manifest, in the same format,
  and runs the same secret scan on every file.
