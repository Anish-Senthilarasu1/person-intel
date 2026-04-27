# Person Intel
<img width="992" height="440" alt="image" src="https://github.com/user-attachments/assets/0667c448-5f20-4dbd-b1bb-7c2701065423" />

`Person Intel` is a terminal-first research engine for understanding how a person operates from their public traces.

The current version is intentionally not a glossy AI profile generator. It collects public data, preserves provenance, and exports a clean markdown corpus plus an Obsidian-ready vault so you can inspect the evidence before layering inference on top.
![Uploading image.png…]()

## What It Does

- Collects public traces from Twitter/X APIs, web search, and public LinkedIn pages
- Deduplicates and chunks the collected material
- Exports a single raw markdown dossier for each person
- Builds a deterministic knowledge pack with:
  - `chunks.jsonl`
  - `entities.json`
  - `links.json`
  - `manifest.json`
- Generates an Obsidian-ready vault with linked notes for:
  - people
  - entities
  - sources
  - chunk-level evidence
- Supports an optional LLM layer later, but does not require one for the core workflow

## Why This Exists

Most people-building tools collapse into shallow sales enrichment or generic “AI bios.”

This project is aiming at a different problem:

- how does a person think?
- what patterns repeat across their public output?
- what people, companies, ideas, and projects cluster around them?
- what evidence might explain how they rose?

The default mode reflects that philosophy. It stores the raw corpus first, so better synthesis, retrieval, or graph logic can be added later without re-scraping the person from scratch.

## Core Workflow

1. Scrape public sources
2. Save raw documents
3. Deduplicate and chunk content
4. Export a markdown dossier
5. Build a local knowledge layer
6. Explore the vault in Obsidian or feed the corpus into later retrieval / reasoning systems

## Repository Status

This repo is now explicitly focused on:

- terminal UI over web UI
- raw data fidelity over polished summaries
- inspectable knowledge artifacts over black-box synthesis

There is no frontend in the active product surface. The project is TUI + markdown + knowledge-pack pipeline.

## Quick Start

### 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in the keys you want to use. The main one for the current workflow is:

- `TWITTERAPI_IO_KEY`

Optional:

- `GROQ_API_KEY` if you want to experiment with the LLM path later
- `BRAVE_SEARCH_API_KEY` for more reliable web search

### 3. Run The TUI

```bash
python3 -m person_intel.tui
```

Or after editable install:

```bash
person-intel-tui
```

### 4. Run The Raw CLI Path

```bash
python3 -m person_intel.cli profile "Rishi Kanaparti" \
  --twitter 0x_rxkvys \
  --sources twitter,web \
  --max-tweets 100 \
  --max-pages 20
```

## Output Layout

Each run writes to:

```text
outputs/<person-slug>/
```

Main artifacts:

- `<slug>_profile.md`
- `raw/`
- `chunks/all_chunks.jsonl`
- `knowledge/manifest.json`
- `knowledge/chunks.jsonl`
- `knowledge/entities.json`
- `knowledge/links.json`
- `knowledge/vault/`

## Obsidian Flow

Open the generated vault folder directly in Obsidian:

```text
outputs/<person-slug>/knowledge/vault
```

From there you can:

- open `index.md`
- navigate through person, entity, source, and chunk notes
- use graph view to inspect relationships
- annotate or extend the vault manually

## Example Direction

This project is most useful when treated as a memory substrate, not a final report generator.

Good downstream uses:

- retrieval over public evidence
- longitudinal tracking of a person over time
- comparing multiple people across shared entities and ideas
- feeding curated context into an LLM only after the evidence layer is assembled
- building an Obsidian-native research workflow

## Roadmap

- Improve source disambiguation
- Reduce duplicate and low-signal web results
- Strengthen deterministic entity extraction
- Add better Obsidian metadata and cross-note linking
- Add optional graph and retrieval layers on top of the raw corpus
- Reintroduce LLM reasoning only where it adds clear value

## Contributing

Contributions are welcome, especially around:

- source quality
- deduplication and ranking
- entity extraction
- Obsidian export quality
- evaluation datasets for person-level understanding

Open an issue or a PR if you want to push the evidence layer, retrieval layer, or knowledge-graph workflow forward.

## Screenshot

If you want a TUI screenshot in the README, drop an image at:

```text
docs/images/tui.png
```

Then replace this placeholder with:

```md
![Person Intel TUI](docs/images/tui.png)
```
