import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        felix: {
          deep: "var(--felix-bg-deep)",
          panel: "var(--felix-bg-panel)",
          accent: "rgb(var(--felix-indigo-rgb) / <alpha-value>)",
          "accent-dim": "rgb(var(--felix-indigo-hover-rgb) / <alpha-value>)",
          "accent-light": "rgb(var(--felix-periwinkle-rgb) / <alpha-value>)",
          warm: "rgb(var(--felix-amber-rgb) / <alpha-value>)",
          bright: "var(--felix-text-bright)",
          heading: "var(--felix-text-heading)",
          muted: "var(--felix-text-muted)",
          dim: "var(--felix-text-dim)",
          ghost: "var(--felix-text-ghost)",
          footer: "var(--felix-text-footer)",
        },
      },
      fontFamily: {
        serif: ["'Instrument Serif'", "serif"],
        sans: ["'DM Sans'", "system-ui", "-apple-system", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
