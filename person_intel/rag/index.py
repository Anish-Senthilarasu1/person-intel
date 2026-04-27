from pathlib import Path

from loguru import logger

from person_intel.storage.models import Chunk, PersonQuery


def build_index(person: PersonQuery, chunks: list[Chunk], output_dir: Path) -> None:
    try:
        import chromadb
    except ImportError:
        logger.warning("chromadb not installed, skipping RAG index")
        return

    chroma_path = output_dir / person.slug / "chroma"
    chroma_path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection_name = person.slug.replace("-", "_")

    # Delete existing collection to rebuild
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    collection = client.create_collection(collection_name)

    ids = [f"chunk_{i}" for i in range(len(chunks))]
    documents = [c.content for c in chunks]
    metadatas = [
        {"source": c.source, "url": c.url, **{k: str(v) for k, v in c.metadata.items()}}
        for c in chunks
    ]

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    logger.info(f"RAG: indexed {len(chunks)} chunks into ChromaDB at {chroma_path}")


def query_index(
    person: PersonQuery, question: str, output_dir: Path, n_results: int = 5
) -> list[Chunk]:
    try:
        import chromadb
    except ImportError:
        logger.error("chromadb not installed")
        return []

    chroma_path = output_dir / person.slug / "chroma"
    if not chroma_path.exists():
        logger.error(f"No RAG index found at {chroma_path}. Run with --rag first.")
        return []

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection_name = person.slug.replace("-", "_")

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        logger.error(f"Collection '{collection_name}' not found in RAG index.")
        return []

    results = collection.query(query_texts=[question], n_results=n_results)
    chunks: list[Chunk] = []

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    for doc, meta in zip(docs, metas):
        chunks.append(
            Chunk(
                source=meta.get("source", "unknown"),
                url=meta.get("url", ""),
                content=doc,
                token_count=len(doc.split()),
                metadata={k: v for k, v in meta.items() if k not in ("source", "url")},
            )
        )

    return chunks
