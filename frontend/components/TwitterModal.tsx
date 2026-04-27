"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  onClose: () => void;
}

type Tab = "twikit" | "cookies";

const COOKIE_STEPS = [
  { n: "01", title: "Open X.com in Chrome", body: "Make sure you're logged in." },
  { n: "02", title: "Open DevTools", body: 'F12 → Application tab → Storage → Cookies → click "https://x.com"' },
  { n: "03", title: "Copy auth_token", body: 'Find the "auth_token" row → copy its Value (long hex string).' },
  { n: "04", title: "Copy ct0", body: 'Find the "ct0" row → copy its Value, then paste both below.' },
];

export default function TwitterModal({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>("twikit");

  const [tkUsername, setTkUsername] = useState("");
  const [tkEmail, setTkEmail] = useState("");
  const [tkPassword, setTkPassword] = useState("");
  const [tkTotp, setTkTotp] = useState("");
  const [tkLoading, setTkLoading] = useState(false);
  const [tkTesting, setTkTesting] = useState(false);
  const [tkResult, setTkResult] = useState<{ ok: boolean; msg: string } | null>(null);

  const [ckUsername, setCkUsername] = useState("");
  const [ckAuthToken, setCkAuthToken] = useState("");
  const [ckCt0, setCkCt0] = useState("");
  const [ckLoading, setCkLoading] = useState(false);
  const [ckTesting, setCkTesting] = useState(false);
  const [ckResult, setCkResult] = useState<{ ok: boolean; msg: string } | null>(null);

  const handleTwikitLogin = async () => {
    if (!tkUsername || !tkPassword) return;
    setTkLoading(true);
    setTkResult(null);
    try {
      const res = await fetch("/api/twitter/twikit-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: tkUsername, email: tkEmail, password: tkPassword, totp_secret: tkTotp || null }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setTkResult({ ok: true, msg: `Logged in as @${data.username}. Cookies saved.` });
      } else {
        setTkResult({ ok: false, msg: data.detail ?? data.message ?? "Login failed" });
      }
    } catch {
      setTkResult({ ok: false, msg: "Network error" });
    } finally {
      setTkLoading(false);
    }
  };

  const handleTwikitTest = async () => {
    setTkTesting(true);
    setTkResult(null);
    try {
      const res = await fetch("/api/twitter/twikit-test", { method: "POST" });
      const data = await res.json();
      setTkResult({ ok: data.status === "ok", msg: data.message });
    } catch {
      setTkResult({ ok: false, msg: "Network error" });
    } finally {
      setTkTesting(false);
    }
  };

  const handleCookieSave = async () => {
    if (!ckUsername || !ckAuthToken || !ckCt0) return;
    setCkLoading(true);
    setCkResult(null);
    try {
      const res = await fetch("/api/twitter/cookies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: ckUsername, auth_token: ckAuthToken, ct0: ckCt0 }),
      });
      const data = await res.json();
      if (res.ok) setCkResult({ ok: true, msg: data.message });
      else setCkResult({ ok: false, msg: data.detail ?? "Failed" });
    } catch {
      setCkResult({ ok: false, msg: "Network error" });
    } finally {
      setCkLoading(false);
    }
  };

  const handleCookieTest = async () => {
    setCkTesting(true);
    setCkResult(null);
    try {
      const res = await fetch("/api/twitter/test", { method: "POST" });
      const data = await res.json();
      setCkResult({
        ok: data.status === "ok",
        msg: data.status === "ok"
          ? `Connected! Accounts: ${(data.accounts as Array<{username: string}>)?.map((a) => `@${a.username}`).join(", ")}`
          : (data.message ?? "Connection failed"),
      });
    } catch {
      setCkResult({ ok: false, msg: "Network error" });
    } finally {
      setCkTesting(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.15)", backdropFilter: "blur(2px)" }}
      />

      {/* Modal */}
      <div
        className="animate-slide-up"
        style={{
          position: "relative",
          background: "#fff",
          borderRadius: "12px",
          border: "1px solid #e5e5e5",
          padding: "2rem",
          width: "100%",
          maxWidth: "480px",
          maxHeight: "90vh",
          overflowY: "auto",
          boxShadow: "0 8px 40px rgba(0,0,0,0.08)",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "1.5rem" }}>
          <div>
            <p className="label text-ink-3 mb-1" style={{ fontSize: "0.75rem" }}>optional setup</p>
            <h2 style={{ fontSize: "1.25rem" }}>twitter auth</h2>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: "1.25rem", color: "#999", lineHeight: 1, padding: "0.25rem" }}
          >
            ×
          </button>
        </div>

        {/* Tab switcher */}
        <div className="flex flex-wrap gap-2 mb-6">
          <button
            onClick={() => setTab("twikit")}
            className={cn("pill", tab === "twikit" && "active")}
          >
            direct login
            {tab !== "twikit" && (
              <span style={{ marginLeft: "0.4rem", fontSize: "0.7rem", color: "#16a34a" }}>✓ recommended</span>
            )}
          </button>
          <button
            onClick={() => setTab("cookies")}
            className={cn("pill", tab === "cookies" && "active")}
          >
            cookie auth
          </button>
        </div>

        {/* twikit tab */}
        {tab === "twikit" && (
          <div className="animate-slide-up">
            <p style={{ fontSize: "0.875rem", color: "#666", lineHeight: 1.6, marginBottom: "1.5rem", padding: "0.75rem", background: "#f5f5f5", borderRadius: "8px" }}>
              <strong style={{ color: "#000" }}>twikit</strong> logs in with your regular X account — no API key, completely free. Cookies are saved so your password is only needed once.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: "1rem", marginBottom: "1.25rem" }}>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>x username (no @)</label>
                <input
                  className="input-box"
                  placeholder="yourhandle"
                  value={tkUsername}
                  onChange={(e) => setTkUsername(e.target.value)}
                  autoComplete="username"
                />
              </div>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>account email <span style={{ color: "#999" }}>— required to bypass bot detection</span></label>
                <input
                  type="email"
                  className="input-box"
                  placeholder="you@email.com"
                  value={tkEmail}
                  onChange={(e) => setTkEmail(e.target.value)}
                  autoComplete="email"
                />
              </div>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>password</label>
                <input
                  type="password"
                  className="input-box"
                  placeholder="Your X account password"
                  value={tkPassword}
                  onChange={(e) => setTkPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>2FA secret (optional)</label>
                <input
                  className="input-box"
                  placeholder="TOTP secret from authenticator app"
                  value={tkTotp}
                  onChange={(e) => setTkTotp(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem" }}>
              <button
                onClick={handleTwikitLogin}
                disabled={tkLoading || !tkUsername || !tkEmail || !tkPassword}
                className="btn"
                style={{ flex: 1 }}
              >
                {tkLoading ? "logging in…" : "login & save cookies"}
              </button>
              <button
                onClick={handleTwikitTest}
                disabled={tkTesting}
                className="btn-outline"
              >
                {tkTesting ? "testing…" : "test"}
              </button>
            </div>

            {tkResult && (
              <div style={{
                padding: "0.75rem",
                borderRadius: "8px",
                fontSize: "0.8125rem",
                background: tkResult.ok ? "#f0fdf4" : "#fef2f2",
                color: tkResult.ok ? "#16a34a" : "#dc2626",
                lineHeight: 1.5,
              }}>
                {tkResult.msg}
              </div>
            )}

            <p style={{ marginTop: "1.25rem", fontSize: "0.75rem", color: "#999", lineHeight: 1.5 }}>
              Credentials go only to your local server (localhost:8000). Only the cookie file at{" "}
              <span style={{ fontFamily: '"Geist Mono", monospace' }}>./data/twikit_cookies.json</span> is kept.
            </p>
          </div>
        )}

        {/* cookies tab */}
        {tab === "cookies" && (
          <div className="animate-slide-up">
            <p style={{ fontSize: "0.875rem", color: "#666", lineHeight: 1.6, marginBottom: "1.5rem" }}>
              Manually extract cookies from your browser to use <strong style={{ color: "#000" }}>twscrape</strong>. More steps, but works as a fallback.
            </p>

            {/* Steps */}
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginBottom: "1.5rem" }}>
              {COOKIE_STEPS.map((step) => (
                <div key={step.n} style={{ display: "flex", gap: "0.875rem", alignItems: "flex-start" }}>
                  <div style={{
                    width: "1.5rem",
                    height: "1.5rem",
                    borderRadius: "4px",
                    background: "#f5f5f5",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    fontFamily: '"Geist Mono", monospace',
                    fontSize: "0.65rem",
                    color: "#666",
                    marginTop: "0.125rem",
                  }}>
                    {step.n}
                  </div>
                  <div>
                    <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#000", marginBottom: "0.125rem" }}>{step.title}</div>
                    <div style={{ fontSize: "0.8125rem", color: "#666" }}>{step.body}</div>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "1rem", marginBottom: "1.25rem" }}>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>x username (no @)</label>
                <input className="input-box" placeholder="yourhandle" value={ckUsername} onChange={(e) => setCkUsername(e.target.value)} />
              </div>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>auth_token value</label>
                <input type="password" className="input-box" placeholder="Paste auth_token here" value={ckAuthToken} onChange={(e) => setCkAuthToken(e.target.value)} />
              </div>
              <div>
                <label className="label text-ink-3 block mb-1.5" style={{ fontSize: "0.75rem" }}>ct0 value</label>
                <input className="input-box" placeholder="Paste ct0 here" value={ckCt0} onChange={(e) => setCkCt0(e.target.value)} />
              </div>
            </div>

            <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem" }}>
              <button
                onClick={handleCookieSave}
                disabled={ckLoading || !ckUsername || !ckAuthToken || !ckCt0}
                className="btn"
                style={{ flex: 1 }}
              >
                {ckLoading ? "saving…" : "save cookies"}
              </button>
              <button
                onClick={handleCookieTest}
                disabled={ckTesting}
                className="btn-outline"
              >
                {ckTesting ? "testing…" : "test"}
              </button>
            </div>

            {ckResult && (
              <div style={{
                padding: "0.75rem",
                borderRadius: "8px",
                fontSize: "0.8125rem",
                background: ckResult.ok ? "#f0fdf4" : "#fef2f2",
                color: ckResult.ok ? "#16a34a" : "#dc2626",
                lineHeight: 1.5,
              }}>
                {ckResult.msg}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
