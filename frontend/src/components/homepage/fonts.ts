import localFont from "next/font/local";

export const newsreader = localFont({
  src: [
    { path: "./fonts/newsreader.woff2", weight: "300 500", style: "normal" },
    { path: "./fonts/newsreader-italic.woff2", weight: "300 500", style: "italic" },
  ],
  variable: "--font-dossier-serif",
  display: "swap",
  preload: true,
  adjustFontFallback: "Times New Roman",
  fallback: ["Georgia", "serif"],
});

export const archivo = localFont({
  src: "./fonts/archivo.woff2",
  weight: "400 600",
  style: "normal",
  variable: "--font-dossier-sans",
  display: "swap",
  preload: true,
  adjustFontFallback: "Arial",
  fallback: ["Arial", "sans-serif"],
});
