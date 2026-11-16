import type { Viewport } from "next";

export const viewport: Viewport = {
  themeColor: "#080f1e",
  colorScheme: "light",
};

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children;
}
