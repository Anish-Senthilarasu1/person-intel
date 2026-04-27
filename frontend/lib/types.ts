export type View = "form" | "pipeline" | "results";

export interface PersonForm {
  name: string;
  twitter_handle: string;
  linkedin_slug: string;
  github_username: string;
  company: string;
  enabled_sources: string[];
  max_tweets: number;
  max_web_pages: number;
  raw_dump: boolean;
  build_rag: boolean;
  twitterapiio_key: string;
}

export interface LogLine {
  id: string;
  message: string;
  kind: "log" | "ok" | "warn" | "err" | "stage";
}

export interface PipelineStats {
  total_chunks: number;
  total_tokens: number;
  by_source: Record<string, number>;
  observation_count?: number;
  timeline_count?: number;
  hypothesis_count?: number;
}

export interface PipelineResult {
  markdown: string;
  stats: PipelineStats;
  output_path: string;
  job_id: string;
}

export const STAGES = [
  { id: 1, label: "scraping", description: "collecting data from sources" },
  { id: 2, label: "dedup", description: "removing duplicates" },
  { id: 3, label: "chunking", description: "breaking into chunks" },
  { id: 4, label: "budget", description: "context window planning" },
  { id: 5, label: "synthesis", description: "building intelligence file" },
];

export const SOURCE_LABELS: Record<string, string> = {
  twitter: "Twitter / X",
  web: "Web",
  github: "GitHub",
  linkedin: "LinkedIn",
  _chunks: "All Chunks",
  _person_model: "Person Model",
};

export const SOURCE_ICONS: Record<string, string> = {
  twitter: "𝕏",
  web: "🌐",
  github: "⬡",
  linkedin: "in",
};
