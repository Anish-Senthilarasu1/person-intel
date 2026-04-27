import re

import tiktoken

from person_intel.storage.models import Chunk, RawDocument

_enc = tiktoken.get_encoding("cl100k_base")

TARGET_TOKENS = 800
OVERLAP_TOKENS = 100


def _count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _split_into_chunks(text: str, source: str, url: str, metadata: dict) -> list[Chunk]:
    """Split text into token-aware chunks with overlap."""
    paragraphs = re.split(r"\n\n+", text.strip())
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_tokens = _count_tokens(para)

        # If a single paragraph exceeds target, split by sentences
        if para_tokens > TARGET_TOKENS:
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", para)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                s_tokens = _count_tokens(sentence)
                if current_tokens + s_tokens > TARGET_TOKENS and current_parts:
                    content = " ".join(current_parts)
                    chunks.append(
                        Chunk(
                            source=source,
                            url=url,
                            content=content,
                            token_count=_count_tokens(content),
                            metadata=metadata,
                        )
                    )
                    # Overlap: keep last ~OVERLAP_TOKENS worth of text
                    overlap_text = content[-400:]
                    current_parts = [overlap_text]
                    current_tokens = _count_tokens(overlap_text)

                current_parts.append(sentence)
                current_tokens += s_tokens
        else:
            if current_tokens + para_tokens > TARGET_TOKENS and current_parts:
                content = "\n\n".join(current_parts)
                chunks.append(
                    Chunk(
                        source=source,
                        url=url,
                        content=content,
                        token_count=_count_tokens(content),
                        metadata=metadata,
                    )
                )
                # Overlap
                overlap_text = current_parts[-1] if current_parts else ""
                current_parts = [overlap_text] if overlap_text else []
                current_tokens = _count_tokens(overlap_text)

            current_parts.append(para)
            current_tokens += para_tokens

    if current_parts:
        content = "\n\n".join(current_parts)
        chunks.append(
            Chunk(
                source=source,
                url=url,
                content=content,
                token_count=_count_tokens(content),
                metadata=metadata,
            )
        )

    return chunks


def chunk_documents(documents: list[RawDocument]) -> list[Chunk]:
    """Convert RawDocuments into token-bounded Chunks."""
    all_chunks: list[Chunk] = []

    for doc in documents:
        if doc.source == "twitter":
            # Twitter batches are already atomic — keep as-is
            all_chunks.append(
                Chunk(
                    source=doc.source,
                    url=doc.url,
                    content=doc.content_raw,
                    token_count=_count_tokens(doc.content_raw),
                    metadata=doc.metadata,
                )
            )
        elif doc.source == "github" and doc.metadata.get("type") == "repo_list":
            # Repo list is structured — keep as-is
            all_chunks.append(
                Chunk(
                    source=doc.source,
                    url=doc.url,
                    content=doc.content_raw,
                    token_count=_count_tokens(doc.content_raw),
                    metadata=doc.metadata,
                )
            )
        else:
            chunks = _split_into_chunks(
                doc.content_raw, doc.source, doc.url, doc.metadata
            )
            all_chunks.extend(chunks)

    return all_chunks
