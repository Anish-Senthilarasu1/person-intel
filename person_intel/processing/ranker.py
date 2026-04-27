from person_intel.storage.models import Chunk, PersonQuery

# Higher priority = kept raw longer; lower priority = summarized first
SOURCE_PRIORITY = {
    "twitter": 4,
    "web": 3,
    "github": 2,
    "linkedin": 1,
}

HIGH_SIGNAL_TERMS = {
    "decision": 2.5,
    "decided": 2.5,
    "started": 2.0,
    "founded": 2.5,
    "launched": 2.0,
    "built": 1.5,
    "shipped": 1.5,
    "joined": 1.5,
    "left": 1.5,
    "pivot": 2.5,
    "risk": 2.0,
    "bet": 2.0,
    "customer": 1.5,
    "distribution": 2.0,
    "growth": 1.5,
    "hiring": 1.5,
    "product": 1.0,
    "strategy": 1.5,
    "belief": 1.5,
    "learned": 1.5,
    "mistake": 2.0,
    "failure": 2.0,
    "success": 1.5,
}


def _relevance_score(chunk: Chunk, person: PersonQuery) -> float:
    text_lower = chunk.content.lower()
    name_lower = person.name.lower()
    score = text_lower.count(name_lower) * 2.0
    if person.company:
        score += text_lower.count(person.company.lower()) * 1.0
    for term, weight in HIGH_SIGNAL_TERMS.items():
        score += text_lower.count(term) * weight
    if any(char.isdigit() for char in chunk.content):
        score += 1.0
    if chunk.metadata.get("type") in {"profile", "readme"}:
        score += 1.0
    if chunk.source == "twitter" and not chunk.is_summary:
        score += 1.0
    return score


class ContextBudget:
    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens

    def fits(self, chunks: list[Chunk]) -> bool:
        return sum(c.token_count for c in chunks) <= self.max_tokens

    def total_tokens(self, chunks: list[Chunk]) -> int:
        return sum(c.token_count for c in chunks)

    def rank_for_compression(
        self, chunks: list[Chunk], person: PersonQuery
    ) -> list[Chunk]:
        """Sort chunks from lowest priority (summarize first) to highest."""
        return sorted(
            chunks,
            key=lambda c: (
                SOURCE_PRIORITY.get(c.source, 0),
                _relevance_score(c, person),
            ),
        )
