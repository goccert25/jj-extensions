# jj-extensions

Utilities to augment jujutsu (jj) with stacked PR workflows on GitHub.

## Tested tool versions

The commands and flags here were tested with:
- uv 0.7.6
- gh 2.72.0
- jj 0.29.0

If your Homebrew installs different major/minor versions and something behaves differently, please open an issue or adjust commands accordingly.

## Install (with uv)

Requirements:
- jj (brew install jj)
- gh (brew install gh) and `gh auth login`
- uv (brew install uv)

Setup the environment and install deps:

```bash
cd /Users/georgetong/code/jj-extensions
uv sync
```

This creates a local `.venv/` and installs the project in editable mode.

## Global install (uv tool)

Install the CLI globally (exposes the `jj-stack` command on your PATH):

```bash
cd /Users/georgetong/code/jj-extensions
uv tool install .
```

Then verify:

```bash
jj-stack --help
```

If the command is not found, ensure your user bin directory is on PATH (uv typically uses `~/.local/bin`):

```bash
# zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && exec zsh
```

To update after local changes, re-run `uv tool install .`.

### Troubleshooting global install

If reinstalling doesn’t reflect your latest source changes (uv can reuse a cached build):

```bash
uv tool uninstall jj-extensions   # uninstall by package name, not script name
uv cache clean                    # clear uv’s cached build
uv tool install .
hash -r                           # refresh shell command cache
```

Confirm what’s being executed:

```bash
which -a jj-stack
head -n3 "$(command -v jj-stack)"
```

During development, prefer running from local source (no reinstall needed):

```bash
uv run jj-stack stack sync --dry-run
```

## Usage

From a jj-enabled git repo:

```bash
uv run jj-stack stack sync
```

Options:
- `--remote origin` select remote
- `--default-base main` set default base if repo default cannot be detected
- `--marker jj-stack-sync` body section marker key
- `--dry-run` do not change GitHub, just compute order

Behavior:
1) Pushes bookmarks via `jj git push --allow-new` (aborts on failure)
2) Ensures each bookmark has a PR
3) Sets PR base to previous branch in stack; bottom targets default branch
4) Upserts a managed section containing the list of PR numbers, with a pointer to the current PR

The managed section is wrapped like:

```
<!-- jj-stack-sync:start -->
- #1322
- 👉 #1321
- #1323
<!-- jj-stack-sync:end -->
```

Your own description content outside this section is preserved.

## Optional: invoke as `jj stack sync`

Add an alias to `~/.jjconfig.toml`:

```toml
[alias]
"stack sync" = "!uv run jj-stack stack sync"
```

If you installed globally with `uv tool install .`, you can skip `uv run`:

```toml
[alias]
"stack sync" = "!jj-stack stack sync"
```

Now you can run:

```bash
jj stack sync
```

## Notes
- Default branch is detected via `gh repo view --json defaultBranchRef` or falls back to git's origin/HEAD; finally `main`.
- Ordering is inferred from the topological order of commits referenced by bookmarks.
- Use `uv add <pkg>` to add dependencies and `uv lock` to regenerate `uv.lock` if needed.
