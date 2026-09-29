# V2 Baseline — Module 1 (Isolated Development Copy)

**Timestamp:** 2026-09-25 11:10:39 -0500

**Source:** `D:\AI\claude-code\claim-dispute-review-copilot`
**Destination:** `D:\AI\claude-code\claim-dispute-review-copilot-v2`

This document records the exact state of the source project at the moment it was
copied into this isolated `v2` development directory, and exactly what Module 1 did
and did not change. The displayed application name remains **"Claim Dispute Review
Copilot"** — the `-v2` suffix applies only to this directory path, not to the app
itself.

## 1. Source Git state (as actually observed, not assumed)

- **Branch:** `main`
- **HEAD (full SHA):** `d057271f6abdc0aee2297764503dfa03698e1b08`
- **HEAD subject:** "Remove docs/DEMO_RUNBOOK.md from the repo"
- **Remote `origin`:** `https://github.com/weishunli2021/claim-dispute-review-copilot.git`
  (plain HTTPS URL, no embedded token or credential — nothing was redacted because
  nothing credential-bearing was present in the URL itself)
- **Original working-tree status at inspection time:** **clean** — `git status --short`
  returned no output; nothing staged, nothing unstaged, nothing untracked. This was
  directly inspected, not assumed from any earlier session record.

Because the working tree was clean, no "preserved uncommitted files" list applies —
there were none to preserve.

## 2. Baseline tag

Working tree was clean, so the requested annotated tag was created (it did not already
exist):

- **Tag:** `dispute-demo-before-v2-20260925`
- **Target commit:** `d057271f6abdc0aee2297764503dfa03698e1b08` (source `main` HEAD at
  copy time)
- **Annotation:** "Baseline before isolated v2 dispute scenarios and golden evaluation
  work."
- **Tagger:** Weishun Li <weishunli2021@gmail.com> (existing local repository identity
  — no identity was invented, and global Git config was not touched)
- **Not pushed.** The tag exists only in the local source repository.

## 3. Copy exclusions (selected after inspecting actual source paths)

The source tree was inspected directly before choosing exclusions (via `ls` and
`find`), rather than assumed. Found and excluded:

| Excluded | Reason | Found at |
|---|---|---|
| `.git/` | Source Git history — destination gets a fresh, independent repository | top level |
| `.venv/` | Python virtual environment | top level |
| `.pytest_cache/` | Test cache | top level |
| `__pycache__/` (recursive, 11 instances) | Python bytecode cache | `agents/`, `application/`, `context/`, `evals/`, `graph/`, `prompts/`, `rag/`, `skills/`, `tests/`, `tools/`, `dispute_review/` |
| `*.pyc` | Compiled Python bytecode (all instances were inside the `__pycache__/` dirs above; none found stray) | — |

Inspected and confirmed **not present** in the source, so no exclusion rule was needed
for them: generated vector stores/indexes (`*.faiss`, `*.index`, `chroma_db/`,
`.chroma/`, `/rag/index/`), knowledge-graph `.db`/`.graphdb` files, `*.log` files,
`.streamlit/secrets.toml`, and build/egg-info artifacts (`dist/`, `build/`,
`*.egg-info/`).

**`.env` was copied directly** (source → destination), per explicit authorization, for
later local use. Its contents were never printed, displayed, or included in this
document.

Copy performed with `robocopy /E /XD .git .venv .pytest_cache __pycache__ /XF *.pyc`.
Robocopy reported exit code **1** ("one or more files copied successfully") — within
the 0–7 nonfatal range; not a failure. Summary: 225 files copied (224 non-`.env` files
verified by hash below, plus `.env`), 0 mismatches, 0 failures, 0 skipped files (14
directories were "skipped" only in the sense that they were the excluded directories
themselves, e.g. `.git`, `.venv`, `.pytest_cache`, and the 11 `__pycache__` dirs).

## 4. Destination Git repository

- **Initialized fresh** with `git init -b main` — no source history copied.
- **Branch:** `main`
- **Commits:** none (Module 1 intentionally creates no commit)
- **Remotes:** none configured
- `.gitignore` and `.env.example` were copied as-is from the source and inspected in
  the destination; both were already correct, so **no ignore/template corrections were
  needed** in the destination.

## 5. File comparison (verification)

All retained non-secret files (source vs. destination) were compared by **SHA-256
hash**, not by name/size/timestamp alone:

- **224 non-secret files hashed on each side.**
- **0 missing files, 0 differing files** — every hash matched exactly (`diff` between
  the full sorted hash lists produced no output).
- `.env` was excluded from this specific hash-diff report (kept out of scope as a
  secret-adjacent file per the task's "nonsecret files" framing) and instead verified
  separately by existence and size: present in both source and destination at
  1,150 bytes, matching modification time — copied, not regenerated or altered.

**Intentional differences:** none. This module adds exactly one new file that has no
counterpart in the source — this document itself
(`docs/V2_BASELINE.md`), created after the hash comparison ran, and therefore correctly
absent from the comparison. No other destination-only files exist yet.

## 6. Secret-protection verification

- `git check-ignore -v .env` in the destination confirms `.env` is matched by
  `.gitignore` (`.gitignore:2:.env`).
- `git ls-files .env` in the destination returns nothing — `.env` is untracked.
- `.env.example` (both source and destination, byte-identical per §5) contains
  placeholders only: `OPENAI_API_KEY=` (blank), `LLM_MODEL=REPLACE_WITH_MODEL_NAME_...`
  (an explicit non-real placeholder), `RAG_EMBEDDING_PROVIDER=tfidf` and
  `LLM_TIMEOUT_SECONDS=30` (safe defaults, not secrets). No real credential value
  appears in this document or was ever printed to any log.

## 7. Application behavior

**Not changed.** Module 1 is a copy-and-initialize operation only: no application
source file was edited, no dependency was installed, no test was run, and no model
call was made. The destination is a byte-identical copy of the source's non-secret
files (§5) plus the source's own `.env`.

## 8. Historical test results (reference only — NOT rerun in Module 1)

The following counts are carried over from the source project's prior validation
sessions. **Historical; not rerun in Module 1:**

- 695 passing tests (historical; not rerun in Module 1)
- End-to-end eval: 8/8 (historical; not rerun in Module 1)
- Safety eval: 9/9 (historical; not rerun in Module 1)

No test suite, evaluation script, or application was executed against this destination
copy as part of Module 1.

## 9. Limitations / unresolved issues

None identified. The source repository's local Git identity (`Weishun Li
<weishunli2021@gmail.com>`) was already configured from an earlier session, so tag
creation did not require prompting for identity or blocking on it. Global Git
configuration was not read for identity purposes and was not modified.
