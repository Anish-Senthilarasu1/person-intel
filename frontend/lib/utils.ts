import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { LogLine } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function classifyLog(message: string): LogLine["kind"] {
  if (message.startsWith("stage:")) return "stage";
  if (message.includes("✓") || message.includes("Done") || message.includes("Saved")) return "ok";
  if (message.includes("⚠") || message.includes("warn") || message.includes("rate limit")) return "warn";
  if (message.includes("✗") || message.includes("FATAL") || message.includes("Error") || message.includes("error")) return "err";
  return "log";
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function generateId(): string {
  return Math.random().toString(36).slice(2, 9);
}
