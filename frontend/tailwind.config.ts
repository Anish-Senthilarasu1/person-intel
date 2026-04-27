import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Geist", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "monospace"],
      },
      colors: {
        paper: "#ffffff",
        ink: "#000000",
        "ink-2": "#444444",
        "ink-3": "#666666",
        "ink-4": "#999999",
        surface: "#f5f5f5",
        "surface-2": "#ebebeb",
        border: "#e5e5e5",
      },
      animation: {
        "fade-in": "fade-in 0.3s ease both",
        "slide-up": "slide-up 0.35s ease both",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      maxWidth: {
        prose: "560px",
        wide: "820px",
      },
      letterSpacing: {
        tight: "-0.03em",
        label: "0.01em",
      },
    },
  },
  plugins: [],
};

export default config;
