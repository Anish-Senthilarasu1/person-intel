"use client";

import { useEffect, useRef } from "react";
import { cn, classifyLog } from "@/lib/utils";
import { STAGES } from "@/lib/types";
import type { LogLine, PipelineResult } from "@/lib/types";

interface Props {
  personName: string;
  currentStage: number;
  logs: LogLine[];
  onDone: (result: PipelineResult) => void;
  onError: (msg: string) => void;
  jobId: string | null;
}

const SOURCE_STAT_PATTERN = /✓ (\w+): (\d+) docs/;

function extractSourceStats(logs: LogLine[]): Record<string, number> {
  const stats: Record<string, number> = {};
  for (const l of logs) {
    const m = l.message.match(SOURCE_STAT_PATTERN);
    if (m) stats[m[1]] = parseInt(m[2]);
  }
  return stats;
}

export default function PipelineProgress({
  personName,
  currentStage,
  logs,
  jobId,
}: Props) {
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const sourceStats = extractSourceStats(logs);
  const visibleLogs = logs.filter((l) => l.kind !== "stage");

  return (
    <div className="min-h-screen flex flex-col px-5 py-16 max-w-prose mx-auto animate-slide-up">

      {/* Header */}
      <div className="mb-10">
        <div className="flex items-center gap-2 mb-3">
          <div className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-black opacity-40" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-black" />
          </div>
          <p className="label text-ink-3">running</p>
        </div>
        <h1>
          profiling{" "}
          <span style={{ color: "#000" }}>{personName}</span>
        </h1>
        <p className="mt-2" style={{ color: "#666", fontSize: "0.875rem" }}>
          this may take 1–3 minutes depending on sources selected.
        </p>

        {/* Source stats */}
        {Object.keys(sourceStats).length > 0 && (
          <div className="flex flex-wrap gap-2 mt-4">
            {Object.entries(sourceStats).map(([src, count]) => (
              <span
                key={src}
                style={{
                  fontSize: "0.75rem",
                  fontFamily: '"Geist Mono", monospace',
                  background: "#f5f5f5",
                  padding: "0.2rem 0.6rem",
                  borderRadius: "100px",
                  color: "#444",
                }}
              >
                {src}: {count}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Stages */}
      <div className="mb-8">
        {STAGES.map((stage, i) => {
          const isDone = currentStage > stage.id;
          const isActive = currentStage === stage.id;
          const isPending = currentStage < stage.id;

          return (
            <div key={stage.id} className={cn("stage-item", isDone && "done", isActive && "active")}>
              <span style={{
                fontFamily: '"Geist Mono", monospace',
                fontSize: "0.75rem",
                minWidth: "1rem",
                color: isDone ? "#16a34a" : isActive ? "#000" : "#ccc",
              }}>
                {isDone ? "✓" : isActive ? "→" : "·"}
              </span>
              <span>{stage.label}</span>
              {isActive && (
                <span style={{ fontSize: "0.75rem", color: "#999", fontWeight: 400 }}>
                  — {stage.description}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Log terminal */}
      <div>
        <p className="label text-ink-3 mb-3" style={{ fontSize: "0.75rem" }}>live log</p>
        <div className="log-box" style={{ height: "360px" }}>
          <div ref={termRef} style={{ height: "100%", overflowY: "auto" }}>
            {visibleLogs.length === 0 ? (
              <div className="log-log">
                <span style={{ animation: "pulse 1s infinite" }}>▌</span> initializing…
              </div>
            ) : (
              visibleLogs.map((line) => (
                <div key={line.id} className={cn("log-" + line.kind)}>
                  {line.message}
                </div>
              ))
            )}
            <div style={{ color: "#ccc", fontSize: "0.75rem", marginTop: "0.25rem" }}>
              <span style={{ animation: "pulse 1s infinite" }}>▌</span>
            </div>
          </div>
        </div>
      </div>

      {jobId && (
        <p className="mt-4" style={{ fontSize: "0.7rem", color: "#ccc", fontFamily: '"Geist Mono", monospace' }}>
          job {jobId.slice(0, 8)}
        </p>
      )}
    </div>
  );
}
