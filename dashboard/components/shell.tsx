"use client";

// Fixed left sidebar + top bar (time range, auto-refresh, last updated, theme).

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { DEFAULT_RANGE, RANGE_KEYS, parseRange, withRange } from "@/lib/range";
import { INTERVALS, useRefresh } from "@/components/refresh";

const NAV = [
  { href: "/", label: "Overview", key: "1" },
  { href: "/live", label: "Live", key: "2" },
  { href: "/debates", label: "Debates", key: "3" },
  { href: "/traces", label: "Traces", key: "4" },
  { href: "/metrics", label: "Metrics", key: "5" },
  { href: "/system", label: "System", key: "6" },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const range = parseRange(useSearchParams().get("range") ?? undefined);

  // Keys 1-6 jump between pages (ignored while typing or with modifier keys).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.ctrlKey || e.metaKey || e.altKey || el?.closest("input, textarea, select, [contenteditable]")) return;
      const item = NAV.find((n) => n.key === e.key);
      if (item) router.push(withRange(item.href, range));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, range]);
  return (
    <nav aria-label="Main" className="flex h-full flex-col">
      <Link href={withRange("/", range)} className="flex h-11 items-center gap-2 border-b border-border px-3">
        <span aria-hidden className="grid size-5 place-items-center bg-accent font-mono text-[11px] font-semibold text-accent-fg">
          A
        </span>
        <span className="text-sm font-semibold tracking-tight">Argus</span>
        <span className="ml-auto font-mono text-[10px] text-ink-3">control room</span>
      </Link>
      <ul className="flex-1 py-2">
        {NAV.map((n) => {
          const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
          return (
            <li key={n.href}>
              <Link
                href={withRange(n.href, range)}
                aria-current={active ? "page" : undefined}
                className={`flex h-8 items-center justify-between border-l-2 px-3 text-sm ${
                  active
                    ? "border-accent bg-surface-2 font-medium text-ink"
                    : "border-transparent text-ink-2 hover:bg-surface-2 hover:text-ink"
                }`}
              >
                {n.label}
                <span className="font-mono text-[10px] text-ink-3">{n.key}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-border px-3 py-2 text-[11px] leading-snug text-ink-3">
        Read API · polling, no WebSockets. Times in UTC.
      </p>
    </nav>
  );
}

function rangeHref(pathname: string, params: URLSearchParams, range: string): string {
  const next = new URLSearchParams(params);
  next.delete("cursor"); // a new range starts at the first page
  if (range === DEFAULT_RANGE) next.delete("range");
  else next.set("range", range);
  const q = next.toString();
  return q ? `${pathname}?${q}` : pathname;
}

function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

export function TopBar() {
  const pathname = usePathname();
  const params = useSearchParams();
  const range = parseRange(params.get("range") ?? undefined);
  const r = useRefresh();
  const now = useNow();
  const ago = r.lastUpdated === null ? null : Math.max(0, Math.round((now - r.lastUpdated) / 1000));

  return (
    <div className="flex h-11 items-center gap-3 border-b border-border bg-surface-1 px-3 text-xs">
      <div role="group" aria-label="Time range" className="flex border border-border">
        {RANGE_KEYS.map((k) => (
          <Link
            key={k}
            href={rangeHref(pathname, params, k)}
            aria-current={k === range ? "true" : undefined}
            className={`num flex h-7 items-center border-r border-border px-2.5 last:border-r-0 ${
              k === range ? "bg-ink text-surface-1" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {k}
          </Link>
        ))}
      </div>

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-pressed={r.enabled}
          onClick={() => r.setEnabled(!r.enabled)}
          className="btn"
          title="Re-fetch data on an interval (paused while the tab is hidden)"
        >
          <span aria-hidden className={`inline-block size-2 border ${r.enabled ? "border-accent bg-accent" : "border-ink-3"}`} />
          Auto-refresh {r.enabled ? "on" : "off"}
        </button>
        <label className="sr-only" htmlFor="refresh-interval">
          Refresh interval
        </label>
        <select
          id="refresh-interval"
          className="field"
          value={r.override ?? r.intervalMs}
          disabled={!r.enabled || r.override !== null}
          onChange={(e) => r.setIntervalMs(Number(e.target.value))}
          title={r.override !== null ? "This page refreshes at a fixed rate" : undefined}
        >
          {[...new Set([...INTERVALS, r.override ?? INTERVALS[0]])].sort((a, b) => a - b).map((ms) => (
            <option key={ms} value={ms}>
              every {ms / 1000}s{r.override === ms ? " (page)" : ""}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={r.refreshNow} disabled={r.refreshing}>
          Refresh
        </button>
      </div>

      <p className="num text-ink-3" aria-live="polite">
        {r.refreshing ? (
          <span className="text-ink-2">updating…</span>
        ) : r.lastUpdated === null ? (
          "—"
        ) : (
          <>
            updated <span className="text-ink-2">{new Date(r.lastUpdated).toISOString().slice(11, 19)}</span> UTC
            <span className="ml-1">({ago}s ago)</span>
          </>
        )}
        {r.hidden && r.enabled && <span className="ml-2 text-warning">paused (tab hidden)</span>}
      </p>

      <div className="ml-auto">
        <ThemeSelect />
      </div>
    </div>
  );
}

const THEME_KEY = "argus.theme";

function ThemeSelect() {
  const [theme, setTheme] = useState("system");
  useEffect(() => {
    try {
      setTheme(localStorage.getItem(THEME_KEY) ?? "system");
    } catch {
      /* ignore */
    }
  }, []);
  const apply = (t: string) => {
    setTheme(t);
    if (t === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    try {
      if (t === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, t);
    } catch {
      /* ignore */
    }
  };
  return (
    <label className="flex items-center gap-1.5 text-ink-3">
      Theme
      <select className="field" value={theme} onChange={(e) => apply(e.target.value)}>
        <option value="system">system</option>
        <option value="light">light</option>
        <option value="dark">dark</option>
      </select>
    </label>
  );
}

/** Applied before first paint (inline in <head>) so a saved theme never flashes. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");var q=new URLSearchParams(location.search).get("theme");t=q||t;if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
