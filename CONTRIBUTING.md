# Contributing — Onboarding & Developer Setup

This document walks you through a reproducible developer setup for the Ultra-Strict project.
Two flows are shown: macOS / Linux and Windows.

> The project uses `uv` as the package & project manager. `uv` supports `uv.lock` for reproducible installs.
> *Use `uv sync --locked` in CI to install exactly what's in `uv.lock`.* See the `uv` docs on locking & syncing.

---

## 1) Prerequisites

- Python 3.12 installed and available on PATH (install via your platform package manager, pyenv, or system installer).
- `git` installed and configured (name & email).
- Recommended: a terminal that supports virtualenv activation (`bash`, `zsh`, PowerShell).

---

## 2) One-time (per-machine) — install `uv`

Open a terminal and run:

```bash
python -m pip install --upgrade pip
python -m pip install "uv"
```

`uv` is the project manager we use; it creates and manages the .venv and lockfile (uv.lock). `uvx` is an alias for `uv tool run` and is convenient for running tools.

---

## 3a) Quick start — macOS / Linux

From the repo root:

```bash
# create a reproducible venv for the project
uv venv

# If the repo already contains uv.lock (recommended), install exact locked deps:
# This will fail if uv.lock is out-of-date (good for CI parity).
if [ -f uv.lock ]; then
  uv sync --locked
else
  # First-time setup: will create uv.lock and install dev dependencies
  uv sync
fi

# Install pre-commit git hooks (local)
# uvx runs tools in ephemeral / consistent envs; but since we installed deps in .venv above,
# using the installed pre-commit is also fine:
uvx pre-commit install || .venv/bin/pre-commit install

# Run initial formatting / checks
uvx ruff format .
uvx pre-commit run --all-files

# Run tests
uvx pytest
```

Notes:

- Use `uv lock` locally to refresh `uv.lock` if you change `pyproject.toml` dependencies. Then commit `uv.lock`.
- To check whether the lockfile matches `pyproject.toml` without changing anything, you can use:

```bash
uv lock --check
```

and in CI we use `uv sync --locked` to guarantee installs match the lockfile.

---

## 3b) Quick start — Windows (PowerShell)

Open PowerShell in repo root:

```powershell
python -m pip install --upgrade pip
python -m pip install uv

uv venv

if (Test-Path uv.lock) {
  uv sync --locked
} else {
  uv sync
}

# Install pre-commit hooks
uvx pre-commit install || .\.venv\Scripts\pre-commit.exe install

# Run formatting and checks
uvx ruff format .
uvx pre-commit run --all-files

# Run tests
uvx pytest
```

---

## 4) Making dependency changes (how to update uv.lock)

When you add or change dependencies in `pyproject.toml`, create / update the lockfile and verify it before pushing:

```bash
# re-lock (resolves and writes uv.lock)
uv lock

# Optionally check the lockfile without changing it:
uv lock --check

# Install from the updated lockfile
uv sync --locked
```

CI policy: our CI runs `uv sync --locked` and will fail if `uv.lock` is out of date. Commit `uv.lock` to the repo after you run `uv lock` locally.

Rationale: using `--locked` in CI guarantees the exact versions are installed and prevents accidental drift between developer machines and CI.

---

## 5) Developer workflow summary

Format & lint locally before committing:

```bash
uvx ruff format .
uvx ruff check . --fix
uvx pre-commit run --all-files
```

Run tests locally:

```bash
uvx pytest
```

If you add or change dependencies:

1.  Run `uv lock`
2.  Commit `pyproject.toml` and `uv.lock`
3.  Run `uv sync --locked` to ensure local venv matches lockfile
4.  Push — CI will run `uv sync --locked` and fail if `uv.lock` is inconsistent.

---

## 6) Troubleshooting / tips

If CI fails with:

```
error: The lockfile at uv.lock needs to be updated
```

run `uv lock` locally and commit the updated `uv.lock`. (This happens when `pyproject.toml` changed without re-locking.)

If you want to test a temporary tool (without installing in your venv), use:

```bash
uvx <tool-name> ...
```

`uvx` runs tools reproducibly in ephemeral environments.
