# Automated data refresh

`update-data.yml` is the GitHub Actions workflow that collects all Form 4
insider transactions from SEC EDGAR, validates them, cross-checks a random
sample against SEC, and commits the generated JSON to `data/`.

## Why it's here and not in `.github/workflows/`

This repository's automation token does not have GitHub's **Workflows**
repository permission, so a workflow file cannot be committed to
`.github/workflows/` from an agent session. The file is kept here so it can be
installed in one step.

## Install

1. Grant the GitHub App / token the **Workflows** repository permission
   (repository settings → GitHub Apps), or use your own credentials.
2. Copy the file into place:

```bash
mkdir -p .github/workflows
cp misc/update-data.yml .github/workflows/update-data.yml
git add .github/workflows/update-data.yml && git commit -m "ci: add data refresh workflow"
git push
```

3. The workflow then runs daily at 03:23 UTC and can also be triggered
   manually (Actions → "Update insider-trade data" → Run workflow).

## What it does

1. `scripts/build_sic_map.py`     — SEC's official SIC → industry-title map.
2. `scripts/fetch_insider_trades.py` — enumerate every Form 4 in the window,
   download each filing's XML, parse all transactions, write `data/*.json`.
3. `scripts/validate_data.py`     — offline validation + `data/validation.json`.
4. `scripts/verify_sample.py`     — re-downloads a random sample from SEC and
   verifies each record line by line → `data/verification.json`.
5. Commits the result back to the branch; GitHub Pages publishes it.
