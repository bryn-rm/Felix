import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        felix: {
          deep: "#080f1e",
          panel: "#0d1526",
          accent: "#6366f1",
          "accent-dim": "#4f46e5",
          bright: "#f0f4ff",
          heading: "#e8edf8",
          muted: "#6b7fa3",
          dim: "#3d4f6b",
          ghost: "#2d3a52",
          footer: "#1e2d45",
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
