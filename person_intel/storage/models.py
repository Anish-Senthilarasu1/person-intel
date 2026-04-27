from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source: str
    url: str
    quote: str | None = None
    confidence: float = 0.5
    timestamp: str | None = None


class Observation(BaseModel):
    kind: str
    summary: str
    rationale: str | None = None
    confidence: float = 0.5
    importance: int = 3
    tags: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    date_label: str
    event: str
    significance: str | None = None
    confidence: float = 0.5
    evidence: list[Evidence] = Field(default_factory=list)


class SuccessHypothesis(BaseModel):
    title: str
    mechanism: str
    confidence: float = 0.5
    supporting_observation_kinds: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class PersonModel(BaseModel):
    observations: list[Observation] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    success_hypotheses: list[SuccessHypothesis] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PersonQuery(BaseModel):
    name: str
    company: str | None = None
    twitter_handle: str | None = None
    linkedin_slug: str | None = None
    github_username: str | None = None
    since_year: int | None = None

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "-")


class RawDocument(BaseModel):
    source: str  # "twitter", "linkedin", "web", "github"
    url: str
    content_raw: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Chunk(BaseModel):
    source: str
    url: str
    content: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_summary: bool = False


class Profile(BaseModel):
    person: PersonQuery
    markdown: str
    sources_used: list[str]
    total_documents: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)
