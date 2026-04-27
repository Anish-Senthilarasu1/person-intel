"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { PersonForm } from "@/lib/types";

interface Props {
  onSubmit: (form: PersonForm) => void;
  loading: boolean;
  onTwitterSetup: () => void;
}

const SOURCES = [
  { id: "twitter", label: "x / twitter" },
  { id: "web",     label: "web" },
  { id: "github",  label: "github" },
  { id: "linkedin",label: "linkedin" },
];

const DEFAULT_FORM: PersonForm = {
  name: "",
  twitter_handle: "",
  linkedin_slug: "",
  github_username: "",
  company: "",
  enabled_sources: ["twitter", "web", "github"],
  max_tweets: 200,
  max_web_pages: 40,
  raw_dump: false,
  build_rag: false,
  twitterapiio_key: "",
};

export default function SearchForm({ onSubmit, loading, onTwitterSetup }: Props) {
  const [form, setForm] = useState<PersonForm>(DEFAULT_FORM);
  const [advanced, setAdvanced] = useState(false);
  const [options, setOptions] = useState(false);

  const set = (key: keyof PersonForm, value: unknown) =>
    setForm((f) => ({ ...f, [key]: value }));

  const toggleSource = (id: string) =>
    set(
      "enabled_sources",
      form.enabled_sources.includes(id)
        ? form.enabled_sources.filter((s) => s !== id)
        : [...form.enabled_sources, id]
    );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    onSubmit(form);
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-5 py-16">
      <div className="w-full max-w-prose">

        {/* Header */}
        <div className="mb-10 animate-slide-up">
          <p className="label text-ink-3 mb-3">person intel</p>
          <h1 style={{ fontSize: "2.25rem", fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.1 }}>
            who do you want<br />to know about?
          </h1>
          <p className="mt-3" style={{ color: "#666", fontSize: "1rem" }}>
            builds an evidence-backed person model from x/twitter, the web, github, and linkedin.
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="animate-slide-up delay-1">

          {/* Name input */}
          <div className="mb-7">
            <label className="label text-ink-3 block mb-2">full name</label>
            <input
              className="input"
              style={{ fontSize: "1.5rem", fontWeight: 500, letterSpacing: "-0.02em" }}
              placeholder="Paul Graham"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              autoFocus
              required
            />
          </div>

          {/* Source toggles */}
          <div className="mb-6">
            <label className="label text-ink-3 block mb-2">sources</label>
            <div className="flex flex-wrap gap-2">
              {SOURCES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => toggleSource(s.id)}
                  className={cn("pill", form.enabled_sources.includes(s.id) && "active")}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* Advanced handles */}
          <div className="mb-4">
            <button
              type="button"
              onClick={() => setAdvanced(!advanced)}
              style={{ fontSize: "0.875rem", color: "#666", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
            >
              {advanced ? "− hide handles" : "+ add handles"}
            </button>

            {advanced && (
              <div className="mt-4 grid grid-cols-2 gap-4 animate-slide-up">
                {[
                  { label: "x / twitter handle", key: "twitter_handle", ph: "paulg" },
                  { label: "github username",     key: "github_username", ph: "paulg" },
                  { label: "linkedin slug",        key: "linkedin_slug",   ph: "pgrahm" },
                  { label: "company / org",        key: "company",         ph: "Y Combinator" },
                ].map(({ label, key, ph }) => (
                  <div key={key}>
                    <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>{label}</label>
                    <input
                      className="input-box"
                      placeholder={ph}
                      value={form[key as keyof PersonForm] as string}
                      onChange={(e) => set(key as keyof PersonForm, e.target.value)}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Options */}
          <div className="mb-8">
            <button
              type="button"
              onClick={() => setOptions(!options)}
              style={{ fontSize: "0.875rem", color: "#666", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
            >
              {options ? "− hide options" : "+ options"}
            </button>

            {options && (
              <div className="mt-4 space-y-5 animate-slide-up">
                {/* Sliders */}
                {[
                  { label: "max tweets", key: "max_tweets",    min: 50,  max: 500, step: 50  },
                  { label: "max pages",  key: "max_web_pages", min: 5,   max: 50,  step: 5   },
                ].map(({ label, key, min, max, step }) => (
                  <div key={key}>
                    <div className="flex justify-between mb-1.5">
                      <span className="label text-ink-3" style={{ fontSize: "0.75rem" }}>{label}</span>
                      <span style={{ fontSize: "0.8125rem", color: "#000", fontFamily: '"Geist Mono", monospace' }}>
                        {form[key as keyof PersonForm] as number}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={min} max={max} step={step}
                      value={form[key as keyof PersonForm] as number}
                      onChange={(e) => set(key as keyof PersonForm, Number(e.target.value))}
                      style={{ width: "100%", accentColor: "#000" }}
                    />
                  </div>
                ))}

                {/* Toggles */}
                <div className="flex gap-6">
                  {[
                    { label: "raw dump", key: "raw_dump",   desc: "skip the person-model synthesis" },
                    { label: "q&a index",key: "build_rag",  desc: "enable rag search after" },
                  ].map(({ label, key, desc }) => (
                    <label key={key} className="flex items-start gap-2 cursor-pointer" style={{ flex: 1 }}>
                      <input
                        type="checkbox"
                        checked={form[key as keyof PersonForm] as boolean}
                        onChange={(e) => set(key as keyof PersonForm, e.target.checked)}
                        style={{ marginTop: "3px", accentColor: "#000", width: "14px", height: "14px", flexShrink: 0 }}
                      />
                      <div>
                        <div style={{ fontSize: "0.875rem", fontWeight: 500 }}>{label}</div>
                        <div style={{ fontSize: "0.75rem", color: "#666" }}>{desc}</div>
                      </div>
                    </label>
                  ))}
                </div>

                {/* API key */}
                <div>
                  <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>
                    twitterapi.io key (optional, ~$0.15/1k tweets)
                  </label>
                  <input
                    type="password"
                    className="input-box"
                    placeholder="leave blank to use free scraping"
                    value={form.twitterapiio_key}
                    onChange={(e) => set("twitterapiio_key", e.target.value)}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button type="submit" className="btn" disabled={loading || !form.name.trim()}>
              {loading ? "starting…" : "generate →"}
            </button>
            {form.enabled_sources.includes("twitter") && (
              <button
                type="button"
                onClick={onTwitterSetup}
                style={{ fontSize: "0.875rem", color: "#666", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", textDecoration: "underline", textUnderlineOffset: "2px" }}
              >
                twitter auth
              </button>
            )}
          </div>
        </form>

        <p className="mt-6 delay-2 animate-fade-in" style={{ fontSize: "0.75rem", color: "#999" }}>
          output saved to ./outputs/{"{name}"}_profile.md
        </p>
      </div>
    </div>
  );
}
