"use client";

import { useState, useCallback } from "react";
import SearchForm from "@/components/SearchForm";
import PipelineProgress from "@/components/PipelineProgress";
import ResultsView from "@/components/ResultsView";
import TwitterModal from "@/components/TwitterModal";
import { classifyLog, generateId } from "@/lib/utils";
import type { View, PersonForm, LogLine, PipelineResult } from "@/lib/types";

export default function Page() {
  const [view, setView] = useState<View>("form");
  const [loading, setLoading] = useState(false);
  const [showTwitterModal, setShowTwitterModal] = useState(false);

  const [jobId, setJobId] = useState<string | null>(null);
  const [personName, setPersonName] = useState("");
  const [personForm, setPersonForm] = useState<PersonForm | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [currentStage, setCurrentStage] = useState(0);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addLog = useCallback((message: string) => {
    const kind = classifyLog(message);
    if (kind === "stage") {
      const n = parseInt(message.replace("stage:", ""));
      setCurrentStage(n);
    }
    setLogs((prev) => [
      ...prev,
      { id: generateId(), message, kind },
    ]);
  }, []);

  const startPipeline = async (form: PersonForm) => {
    setLoading(true);
    setError(null);
    setLogs([]);
    setCurrentStage(1);
    setPersonName(form.name);
    setPersonForm(form);

    try {
      // Start the job
      const startRes = await fetch("/api/profile/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          twitter_handle: form.twitter_handle || null,
          linkedin_slug: form.linkedin_slug || null,
          github_username: form.github_username || null,
          company: form.company || null,
          enabled_sources: form.enabled_sources,
          max_tweets: form.max_tweets,
          max_web_pages: form.max_web_pages,
          raw_dump: form.raw_dump,
          build_rag: form.build_rag,
          twitterapiio_key: form.twitterapiio_key || null,
        }),
      });

      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({ detail: "Failed to start pipeline" }));
        throw new Error(err.detail ?? "Failed to start pipeline");
      }

      const { job_id } = await startRes.json();
      setJobId(job_id);
      setLoading(false);
      setView("pipeline");

      // Connect to SSE stream
      const es = new EventSource(`/api/profile/${job_id}/stream`);

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === "log") {
            addLog(data.message);
          } else if (data.type === "done") {
            es.close();
            const pipelineResult: PipelineResult = {
              markdown: data.markdown,
              stats: data.stats,
              output_path: data.output_path,
              job_id,
            };
            setResult(pipelineResult);
            setCurrentStage(6); // all done
            // Small delay for visual satisfaction
            setTimeout(() => setView("results"), 800);
          } else if (data.type === "error") {
            es.close();
            addLog(`FATAL: ${data.message}`);
            setError(data.message);
          }
          // "ping" events are no-ops — keep connection alive
        } catch {
          // Ignore parse errors from malformed events
        }
      };

      es.onerror = () => {
        es.close();
        addLog("Connection to pipeline lost.");
        setError("Stream connection failed. The pipeline may have crashed — check server logs.");
      };
    } catch (e) {
      setLoading(false);
      setError(e instanceof Error ? e.message : "Unknown error");
      addLog(`Error: ${e instanceof Error ? e.message : "Unknown error"}`);
    }
  };

  const handleReset = () => {
    setView("form");
    setJobId(null);
    setLogs([]);
    setCurrentStage(0);
    setResult(null);
    setError(null);
    setLoading(false);
  };

  return (
    <>
      {view === "form" && (
        <SearchForm
          onSubmit={startPipeline}
          loading={loading}
          onTwitterSetup={() => setShowTwitterModal(true)}
        />
      )}

      {view === "pipeline" && (
        <>
          {error && (
            <div style={{ position: "fixed", top: "1rem", right: "1rem", zIndex: 50, maxWidth: "20rem", padding: "1rem", background: "#fff", border: "1px solid #e5e5e5", borderRadius: "10px", boxShadow: "0 4px 20px rgba(0,0,0,0.08)", fontSize: "0.8125rem" }}>
              <div style={{ fontWeight: 500, color: "#dc2626", marginBottom: "0.25rem" }}>pipeline error</div>
              <div style={{ color: "#666" }}>{error}</div>
              <button
                onClick={handleReset}
                style={{ marginTop: "0.5rem", fontSize: "0.75rem", color: "#000", background: "none", border: "none", cursor: "pointer", textDecoration: "underline", textUnderlineOffset: "2px", padding: 0 }}
              >
                start over
              </button>
            </div>
          )}
          <PipelineProgress
            personName={personName}
            currentStage={currentStage}
            logs={logs}
            onDone={(r) => setResult(r)}
            onError={(msg) => setError(msg)}
            jobId={jobId}
          />
        </>
      )}

      {view === "results" && result && personForm && (
        <ResultsView
          result={result}
          personName={personName}
          person={personForm}
          onReset={handleReset}
        />
      )}

      {showTwitterModal && (
        <TwitterModal onClose={() => setShowTwitterModal(false)} />
      )}
    </>
  );
}
