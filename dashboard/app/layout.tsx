import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { Suspense } from "react";
import { RefreshProvider } from "@/components/refresh";
import { Sidebar, THEME_SCRIPT, TopBar } from "@/components/shell";
import "./globals.css";

// Self-hosted at build time by next/font (no requests to Google from the browser).
const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-sans" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: "Argus",
  description: "Live traces, cost, latency and pipeline health for LLM applications",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="font-sans text-sm antialiased">
        <RefreshProvider>
          <aside className="fixed inset-y-0 left-0 z-20 w-48 border-r border-border bg-surface-1">
            <Suspense fallback={null}>
              <Sidebar />
            </Suspense>
          </aside>
          <div className="pl-48">
            <header className="sticky top-0 z-10">
              <Suspense fallback={<div className="h-11 border-b border-border bg-surface-1" />}>
                <TopBar />
              </Suspense>
            </header>
            <main className="p-3">{children}</main>
          </div>
        </RefreshProvider>
      </body>
    </html>
  );
}
