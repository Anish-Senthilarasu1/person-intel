"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatTokens, cn } from "@/lib/utils";
import type { PipelineResult, PersonForm } from "@/lib/types";
import { SOURCE_LABELS } from "@/lib/types";

interface Props {
  result: PipelineResult;
  personName: string;
  person: PersonForm;
  onReset: () => void;
}

export default function ResultsView({ result, personName, person, onReset }: Props) {
  const [activeTab, setActiveTab] = useState<"profile" | "downloads">("profile");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [qaLoading, setQaLoading] = useState(false);
  const [qaError, setQaError] = useState<string | null>(null);

  // Strip frontmatter
  const lines = result.markdown.split("\n");
  let displayMd = result.markdown;
  if (lines[0]?.trim() === "---") {
    const end = lines.indexOf("---", 1);
    if (end > 0) displayMd = lines.slice(end + 1).join("\n").trim();
  }

  const handleDownloadMd = () => {
    const a = document.createElement("a");
    a.href = `/api/profile/${result.job_id}/download/md`;
    a.download = `${personName.toLowerCase().replace(/\s+/g, "-")}_profile.md`;
    a.click();
  };

  const handleDownloadSource = (source: string) => {
    const a = document.createElement("a");
    a.href = `/api/profile/${result.job_id}/download/${source}`;
    a.download = `${source}.${source === "_chunks" ? "jsonl" : "json"}`;
    a.click();
  };

  const handleAsk = async () => {
    if (!question.trim()) return;
    setQaLoading(true);
    setQaError(null);
    setAnswer(null);
    try {
      const res = await fetch("/api/qa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: question.trim(),
          person: {
            name: person.name,
            twitter_handle: person.twitter_handle || null,
            linkedin_slug: person.linkedin_slug || null,
            github_username: person.github_username || null,
            company: person.company || null,
          },
        }),
      });
      const data = await res.json();
      if (data.error) setQaError(data.error);
      else setAnswer(data.answer);
    } catch {
      setQaError("Failed to reach server.");
    } finally {
      setQaLoading(false);
    }
  };

  const { stats } = result;

  return (
    <div className="min-h-screen flex flex-col px-5 py-16 animate-slide-up" style={{ maxWidth: "860px", margin: "0 auto" }}>

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <p className="label text-ink-3 mb-1">complete</p>
          <h1 style={{ fontSize: "1.75rem" }}>{personName}</h1>
          <p style={{ fontSize: "0.75rem", color: "#999", marginTop: "0.25rem", fontFamily: '"Geist Mono", monospace' }}>
            {result.output_path}
          </p>
        </div>

        <div className="flex items-center gap-3" style={{ marginTop: "0.5rem" }}>
          <button
            onClick={handleDownloadMd}
            className="btn"
          >
            download .md
          </button>
          <button
            onClick={onReset}
            className="btn-outline"
          >
            new profile
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <div className="flex flex-wrap gap-3 mb-6" style={{ fontSize: "0.8125rem", color: "#666", fontFamily: '"Geist Mono", monospace' }}>
        <span><strong style={{ color: "#000" }}>{stats.total_chunks}</strong> chunks</span>
        <span style={{ color: "#ddd" }}>·</span>
        <span><strong style={{ color: "#000" }}>~{formatTokens(stats.total_tokens)}</strong> tokens</span>
        {typeof stats.observation_count === "number" && (
          <>
            <span style={{ color: "#ddd" }}>·</span>
            <span><strong style={{ color: "#000" }}>{stats.observation_count}</strong> observations</span>
          </>
        )}
        {typeof stats.hypothesis_count === "number" && (
          <>
            <span style={{ color: "#ddd" }}>·</span>
            <span><strong style={{ color: "#000" }}>{stats.hypothesis_count}</strong> hypotheses</span>
          </>
        )}
        {Object.entries(stats.by_source).map(([src, count]) => (
          <>
            <span key={src + "-sep"} style={{ color: "#ddd" }}>·</span>
            <span key={src}><strong style={{ color: "#000" }}>{count}</strong> {SOURCE_LABELS[src] ?? src}</span>
          </>
        ))}
      </div>

      {/* Tab switcher */}
      <div className="flex gap-2 mb-6">
        {(["profile", "downloads"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn("pill", activeTab === tab && "active")}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === "profile" && (
        <div className="animate-slide-up">
          {/* Markdown content */}
          <div className="prose mb-10">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayMd}</ReactMarkdown>
          </div>

          {/* Source breakdown */}
          <div style={{ borderTop: "1px solid #e5e5e5", paddingTop: "2rem", marginBottom: "2rem" }}>
            <p className="label text-ink-3 mb-4" style={{ fontSize: "0.75rem" }}>source breakdown</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "1rem" }}>
              {Object.entries(stats.by_source).map(([src, count]) => {
                const total = stats.total_chunks;
                const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                return (
                  <div key={src}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.375rem" }}>
                      <span style={{ fontSize: "0.8125rem", color: "#444" }}>{SOURCE_LABELS[src] ?? src}</span>
                      <span style={{ fontSize: "0.75rem", color: "#999", fontFamily: '"Geist Mono", monospace' }}>{pct}%</span>
                    </div>
                    <div style={{ height: "2px", background: "#e5e5e5", borderRadius: "2px" }}>
                      <div style={{ height: "100%", background: "#000", borderRadius: "2px", width: `${pct}%`, transition: "width 0.7s ease" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Q&A */}
          <div style={{ borderTop: "1px solid #e5e5e5", paddingTop: "2rem" }}>
            <p className="label text-ink-3 mb-4" style={{ fontSize: "0.75rem" }}>ask a question</p>
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <input
                className="input"
                placeholder="What does this person care about?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAsk()}
                style={{ flex: 1 }}
              />
              <button
                className="btn"
                onClick={handleAsk}
                disabled={qaLoading || !question.trim()}
              >
                {qaLoading ? "thinking…" : "ask →"}
              </button>
            </div>
            {qaError && (
              <p style={{ marginTop: "0.75rem", fontSize: "0.875rem", color: "#dc2626" }}>{qaError}</p>
            )}
            {answer && (
              <div style={{ marginTop: "1rem", fontSize: "0.9375rem", color: "#444", lineHeight: 1.7, borderTop: "1px solid #e5e5e5", paddingTop: "1rem" }}>
                {answer}
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === "downloads" && (
        <div className="animate-slide-up">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem", marginBottom: "2rem" }}>
            {/* Main MD file */}
            <button
              onClick={handleDownloadMd}
              className="item-row"
              style={{ textAlign: "left", background: "none", border: "1px solid #e5e5e5", borderRadius: "8px", cursor: "pointer", padding: "1rem 1.25rem" }}
            >
              <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#000", marginBottom: "0.25rem" }}>
                intelligence file
              </div>
              <div style={{ fontSize: "0.75rem", color: "#999", fontFamily: '"Geist Mono", monospace' }}>
                {personName.toLowerCase().replace(/\s+/g, "-")}_profile.md
              </div>
            </button>

            {/* Source files */}
            {Object.keys(stats.by_source).map((src) => (
              <button
                key={src}
                onClick={() => handleDownloadSource(src)}
                className="item-row"
                style={{ textAlign: "left", background: "none", border: "1px solid #e5e5e5", borderRadius: "8px", cursor: "pointer", padding: "1rem 1.25rem" }}
              >
                <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#000", marginBottom: "0.25rem" }}>
                  {SOURCE_LABELS[src] ?? src}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#999", fontFamily: '"Geist Mono", monospace' }}>
                  {src}.json · {stats.by_source[src]} chunks
                </div>
              </button>
            ))}

            {typeof stats.observation_count === "number" && (
              <button
                onClick={() => handleDownloadSource("_person_model")}
                className="item-row"
                style={{ textAlign: "left", background: "none", border: "1px solid #e5e5e5", borderRadius: "8px", cursor: "pointer", padding: "1rem 1.25rem" }}
              >
                <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#000", marginBottom: "0.25rem" }}>
                  structured person model
                </div>
                <div style={{ fontSize: "0.75rem", color: "#999", fontFamily: '"Geist Mono", monospace' }}>
                  person_model.json · {stats.observation_count} observations
                </div>
              </button>
            )}

            <button
              onClick={() => handleDownloadSource("_chunks")}
              className="item-row"
              style={{ textAlign: "left", background: "none", border: "1px solid #e5e5e5", borderRadius: "8px", cursor: "pointer", padding: "1rem 1.25rem" }}
            >
              <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#000", marginBottom: "0.25rem" }}>
                all chunks
              </div>
              <div style={{ fontSize: "0.75rem", color: "#999", fontFamily: '"Geist Mono", monospace' }}>
                all_chunks.jsonl · {stats.total_chunks} chunks
              </div>
            </button>
          </div>

          {/* Raw preview */}
          <div>
            <p className="label text-ink-3 mb-3" style={{ fontSize: "0.75rem" }}>raw markdown preview</p>
            <div className="log-box" style={{ maxHeight: "400px", overflowY: "auto" }}>
              <pre style={{ fontSize: "0.75rem", color: "#444", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                {result.markdown.slice(0, 8000)}
                {result.markdown.length > 8000 && (
                  <span style={{ color: "#999" }}>
                    {"\n\n"}… ({result.markdown.length.toLocaleString()} chars total)
                  </span>
                )}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
