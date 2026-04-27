from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from person_intel.storage.models import Chunk, PersonQuery

HANDLE_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{2,32})")
CAPITALIZED_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:[-'][A-Za-z0-9]+)?(?:\s+|$)){2,4}")
STOP_PHRASES = {
    "Raw Intelligence Dump",
    "Generated",
    "Web Pages",
    "GitHub",
    "LinkedIn",
    "Sources",
    "Summary",
    "Tweets",
}


@dataclass
class Entity:
    id: str
    label: str
    kind: str
    aliases: set[str]
    chunk_ids: set[str]
    source_urls: set[str]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def _safe_note_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]+', "-", text).strip() or "Untitled"


def _trim(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _wiki_link(path_stem: str, label: str | None = None) -> str:
    if label and label != path_stem:
        return f"[[{path_stem}|{label}]]"
    return f"[[{path_stem}]]"


def _chunk_id(index: int) -> str:
    return f"chunk-{index:04d}"


def _extract_phrase_entities(text: str) -> set[str]:
    matches = {
        " ".join(match.group(0).split()).strip()
        for match in CAPITALIZED_PHRASE_RE.finditer(text)
    }
    cleaned = set()
    for phrase in matches:
        if phrase in STOP_PHRASES:
            continue
        if len(phrase) < 5:
            continue
        cleaned.add(phrase)
    return cleaned


def _extract_entities_from_chunk(chunk: Chunk) -> list[tuple[str, str]]:
    entities: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, label: str) -> None:
        label = " ".join(label.split()).strip()
        if not label:
            return
        key = (kind, label.lower())
        if key in seen:
            return
        seen.add(key)
        entities.append((kind, label))

    parsed = urlparse(chunk.url)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]

    if host in {"x.com", "twitter.com"} and path_parts:
        add("twitter_handle", f"@{path_parts[0]}")
    elif host == "github.com":
        if len(path_parts) >= 1:
            add("github_user", path_parts[0])
        if len(path_parts) >= 2:
            add("github_repo", f"{path_parts[0]}/{path_parts[1]}")
    elif "linkedin.com" in host and path_parts:
        add("linkedin_profile", "/".join(path_parts[:2]))
    elif host:
        add("domain", host)

    for handle in HANDLE_RE.findall(chunk.content):
        add("twitter_handle", f"@{handle}")

    repo_match = re.findall(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", chunk.content)
    for repo in repo_match[:10]:
        if "/" in repo and not repo.startswith("http"):
            add("github_repo", repo)

    for phrase in sorted(_extract_phrase_entities(chunk.content))[:12]:
        add("mention", phrase)

    return entities


def _build_entities(person: PersonQuery, chunks: list[Chunk]) -> dict[str, Entity]:
    entities: dict[str, Entity] = {}

    def ensure(kind: str, label: str, chunk_id: str, url: str) -> None:
        entity_id = f"{kind}:{_slugify(label)}"
        entity = entities.get(entity_id)
        if entity is None:
            entity = Entity(
                id=entity_id,
                label=label,
                kind=kind,
                aliases={label},
                chunk_ids=set(),
                source_urls=set(),
            )
            entities[entity_id] = entity
        entity.aliases.add(label)
        entity.chunk_ids.add(chunk_id)
        entity.source_urls.add(url)

    person_id = f"person:{person.slug}"
    entities[person_id] = Entity(
        id=person_id,
        label=person.name,
        kind="person",
        aliases={person.name},
        chunk_ids=set(),
        source_urls=set(),
    )

    for idx, chunk in enumerate(chunks, start=1):
        chunk_id = _chunk_id(idx)
        entities[person_id].chunk_ids.add(chunk_id)
        entities[person_id].source_urls.add(chunk.url)
        for kind, label in _extract_entities_from_chunk(chunk):
            ensure(kind, label, chunk_id, chunk.url)

    return entities


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True))


def _write_chunk_records(knowledge_dir: Path, chunks: list[Chunk]) -> list[dict]:
    records: list[dict] = []
    chunk_file = knowledge_dir / "chunks.jsonl"
    lines: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        record = {
            "id": _chunk_id(idx),
            "source": chunk.source,
            "url": chunk.url,
            "token_count": chunk.token_count,
            "metadata": chunk.metadata,
            "content": chunk.content,
        }
        records.append(record)
        lines.append(json.dumps(record, ensure_ascii=True))
    chunk_file.write_text("\n".join(lines))
    return records


def _write_entities_and_links(
    knowledge_dir: Path,
    person: PersonQuery,
    chunks: list[Chunk],
    chunk_records: list[dict],
) -> tuple[list[dict], list[dict]]:
    entities = _build_entities(person, chunks)
    entity_records = [
        {
            "id": entity.id,
            "label": entity.label,
            "kind": entity.kind,
            "aliases": sorted(entity.aliases),
            "chunk_ids": sorted(entity.chunk_ids),
            "source_urls": sorted(entity.source_urls),
        }
        for entity in sorted(entities.values(), key=lambda item: (item.kind, item.label.lower()))
    ]
    _write_json(knowledge_dir / "entities.json", entity_records)

    person_id = f"person:{person.slug}"
    links: list[dict] = []
    for entity in entity_records:
        if entity["id"] == person_id:
            continue
        shared = entity["chunk_ids"]
        if not shared:
            continue
        links.append(
            {
                "source": person_id,
                "target": entity["id"],
                "relation": "mentions",
                "weight": len(shared),
                "chunk_ids": shared,
            }
        )

    # Co-occurrence links across non-person entities.
    chunk_entities: dict[str, list[str]] = defaultdict(list)
    for entity in entity_records:
        for chunk_id in entity["chunk_ids"]:
            chunk_entities[chunk_id].append(entity["id"])

    pair_weights: dict[tuple[str, str], set[str]] = defaultdict(set)
    for chunk_id, entity_ids in chunk_entities.items():
        filtered = [entity_id for entity_id in entity_ids if entity_id != person_id]
        for i, left in enumerate(filtered):
            for right in filtered[i + 1 :]:
                pair = tuple(sorted((left, right)))
                pair_weights[pair].add(chunk_id)

    for (left, right), chunk_ids in sorted(pair_weights.items()):
        if len(chunk_ids) < 2:
            continue
        links.append(
            {
                "source": left,
                "target": right,
                "relation": "co_occurs",
                "weight": len(chunk_ids),
                "chunk_ids": sorted(chunk_ids),
            }
        )

    _write_json(knowledge_dir / "links.json", links)
    return entity_records, links


def _source_note_name(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "source"
    path = parsed.path.strip("/").replace("/", "-")
    raw = f"{host}-{path}" if path else host
    return _safe_note_name(raw)[:120]


def _write_vault(
    vault_dir: Path,
    person: PersonQuery,
    chunk_records: list[dict],
    entity_records: list[dict],
    links: list[dict],
) -> None:
    people_dir = vault_dir / "People"
    entities_dir = vault_dir / "Entities"
    sources_dir = vault_dir / "Sources"
    chunks_dir = vault_dir / "Chunks"
    for directory in (people_dir, entities_dir, sources_dir, chunks_dir):
        directory.mkdir(parents=True, exist_ok=True)

    person_note = f"People/{_safe_note_name(person.name)}"
    person_entity_id = f"person:{person.slug}"

    source_to_chunks: dict[str, list[dict]] = defaultdict(list)
    for record in chunk_records:
        source_to_chunks[record["url"]].append(record)

    entity_to_note: dict[str, str] = {}
    for entity in entity_records:
        note_stem = f"Entities/{_safe_note_name(entity['label'])}"
        entity_to_note[entity["id"]] = note_stem
        outbound = [link for link in links if link["source"] == entity["id"]]
        lines = [
            "---",
            f"type: {entity['kind']}",
            f"entity_id: {entity['id']}",
            f"label: {entity['label']}",
            f"chunk_count: {len(entity['chunk_ids'])}",
            "---",
            "",
            f"# {entity['label']}",
            "",
            f"- Kind: `{entity['kind']}`",
            f"- Person: {_wiki_link(person_note.split('/', 1)[1], person.name)}",
            "",
            "## Mentions",
        ]
        for chunk_id in entity["chunk_ids"][:20]:
            lines.append(f"- [[Chunks/{chunk_id}]]")
        if outbound:
            lines.extend(["", "## Connections"])
            for link in outbound[:20]:
                target_note = entity_to_note.get(link["target"])
                target_label = next(
                    (item["label"] for item in entity_records if item["id"] == link["target"]),
                    link["target"],
                )
                if target_note:
                    lines.append(
                        f"- {link['relation']} {_wiki_link(target_note, target_label)} "
                        f"(weight {link['weight']})"
                    )
        (vault_dir / f"{note_stem}.md").write_text("\n".join(lines))

    source_note_map: dict[str, str] = {}
    for url, records in source_to_chunks.items():
        note_stem = f"Sources/{_source_note_name(url)}"
        source_note_map[url] = note_stem
        lines = [
            "---",
            "type: source",
            f"url: {url}",
            f"source_kind: {records[0]['source']}",
            f"chunk_count: {len(records)}",
            "---",
            "",
            f"# {url}",
            "",
            f"- Kind: `{records[0]['source']}`",
            f"- Person: {_wiki_link(person_note.split('/', 1)[1], person.name)}",
            "",
            "## Chunks",
        ]
        for record in records:
            lines.append(f"- [[Chunks/{record['id']}]]")
        (vault_dir / f"{note_stem}.md").write_text("\n".join(lines))

    for record in chunk_records:
        chunk_note = chunks_dir / f"{record['id']}.md"
        mentioned_entities = [
            entity for entity in entity_records if record["id"] in entity["chunk_ids"] and entity["id"] != person_entity_id
        ]
        lines = [
            "---",
            "type: chunk",
            f"chunk_id: {record['id']}",
            f"source: {record['source']}",
            f"url: {record['url']}",
            f"token_count: {record['token_count']}",
            "---",
            "",
            f"# {record['id']}",
            "",
            f"- Person: {_wiki_link(person_note.split('/', 1)[1], person.name)}",
            f"- Source note: {_wiki_link(source_note_map[record['url']], record['source'])}",
        ]
        if mentioned_entities:
            lines.extend(["", "## Entities"])
            for entity in mentioned_entities[:15]:
                lines.append(f"- {_wiki_link(entity_to_note[entity['id']], entity['label'])}")
        lines.extend(["", "## Content", "", record["content"]])
        chunk_note.write_text("\n".join(lines))

    person_lines = [
        "---",
        "type: person",
        f"name: {person.name}",
        f"slug: {person.slug}",
        f"twitter_handle: {person.twitter_handle or ''}",
        f"github_username: {person.github_username or ''}",
        f"linkedin_slug: {person.linkedin_slug or ''}",
        f"company: {person.company or ''}",
        "---",
        "",
        f"# {person.name}",
        "",
        "## Sources",
    ]
    for url, note_stem in sorted(source_note_map.items()):
        person_lines.append(f"- {_wiki_link(note_stem, urlparse(url).netloc or url)}")
    person_lines.extend(["", "## Entities"])
    for entity in entity_records:
        if entity["id"] == person_entity_id:
            continue
        person_lines.append(f"- {_wiki_link(entity_to_note[entity['id']], entity['label'])}")
    (vault_dir / f"{person_note}.md").write_text("\n".join(person_lines))

    index_lines = [
        "# Vault Index",
        "",
        f"- Person: {_wiki_link(person_note, person.name)}",
        f"- Total sources: {len(source_note_map)}",
        f"- Total chunks: {len(chunk_records)}",
        f"- Total entities: {len(entity_records)}",
        "",
        "## Top Entities",
    ]
    top_entities = sorted(
        [entity for entity in entity_records if entity["id"] != person_entity_id],
        key=lambda item: len(item["chunk_ids"]),
        reverse=True,
    )[:25]
    for entity in top_entities:
        index_lines.append(
            f"- {_wiki_link(entity_to_note[entity['id']], entity['label'])} "
            f"({len(entity['chunk_ids'])} chunks)"
        )
    (vault_dir / "index.md").write_text("\n".join(index_lines))


def build_knowledge_pack(
    person: PersonQuery,
    chunks: list[Chunk],
    output_dir: Path,
) -> Path:
    knowledge_dir = output_dir / person.slug / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    chunk_records = _write_chunk_records(knowledge_dir, chunks)
    entity_records, links = _write_entities_and_links(knowledge_dir, person, chunks, chunk_records)
    _write_json(
        knowledge_dir / "manifest.json",
        {
            "person": person.model_dump(),
            "chunk_count": len(chunk_records),
            "entity_count": len(entity_records),
            "link_count": len(links),
            "files": [
                "chunks.jsonl",
                "entities.json",
                "links.json",
                "manifest.json",
                "vault/index.md",
            ],
        },
    )
    _write_vault(knowledge_dir / "vault", person, chunk_records, entity_records, links)
    return knowledge_dir
