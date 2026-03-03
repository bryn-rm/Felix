import type { Metadata } from "next";
import ServiceWorkerRegistrar from "@/components/ServiceWorkerRegistrar";

export const metadata: Metadata = {
  title: "Felix",
  themeColor: "#0f172a",
  manifest: "/manifest.json",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>
        <ServiceWorkerRegistrar />
        {children}
      </body>
    </html>
  );
}
