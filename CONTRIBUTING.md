# Contributing

This project is currently centered on a simple principle: preserve public evidence first, then build better tooling on top of it.

If you want to contribute, the highest-value areas right now are:

- source reliability and disambiguation
- deterministic entity extraction
- duplicate suppression
- better markdown and Obsidian exports
- evaluation of whether the exported corpus is actually useful downstream

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run the TUI:

```bash
python3 -m person_intel.tui
```

Run the CLI:

```bash
python3 -m person_intel.cli profile "Example Person" --sources web
```

## Ground Rules

- Prefer improving the evidence layer before adding more inference.
- Preserve provenance whenever possible.
- Avoid brittle browser automation paths for core data collection.
- Keep outputs inspectable by humans.

## Pull Requests

Small, focused PRs are better than broad rewrites.

If you change the data model or export shape, include:

- what changed
- why it improves the downstream knowledge workflow
- how to validate it locally
