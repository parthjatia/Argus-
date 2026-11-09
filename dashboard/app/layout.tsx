import type { Metadata } from "next";
import Link from "next/link";
import { NavLink } from "@/components/NavLink";
import "./globals.css";

export const metadata: Metadata = {
  title: "Argus",
  description: "Traces, cost, latency and errors for LLM applications",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="sticky top-0 z-10 border-b border-border bg-surface-1/90 backdrop-blur">
          <nav className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
            <Link href="/traces" className="flex items-center gap-2 font-semibold tracking-tight">
              <span aria-hidden className="grid size-6 place-items-center rounded-md bg-accent text-xs text-white">
                ◉
              </span>
              Argus
            </Link>
            <div className="flex items-center gap-1 text-sm">
              <NavLink href="/traces">Traces</NavLink>
              <NavLink href="/metrics">Metrics</NavLink>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
