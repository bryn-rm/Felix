import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        felix: {
          background: "#0f172a",
          surface: "#1e293b",
          accent: "#4f46e5",
          "text-primary": "#f1f5f9",
          "text-muted": "#94a3b8",
        },
      },
    },
  },
  plugins: [],
};

export default config;
