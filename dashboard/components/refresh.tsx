"use client";

// Polling without WebSockets: every N seconds we call router.refresh(), which re-renders
// the page's server components with fresh API data. Client state (selected span, open
// tabs, scroll) survives, and the API key stays on the server. Polling pauses while the
// browser tab is hidden and catches up as soon as it becomes visible again.

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, useTransition } from "react";

export const INTERVALS = [2000, 5000, 10000, 30000, 60000] as const;
const STORAGE_KEY = "argus.refresh";

interface RefreshState {
  enabled: boolean;
  intervalMs: number;
  override: number | null; // a page that needs a fixed rate (Live: 2 s)
  effectiveMs: number;
  lastUpdated: number | null;
  refreshing: boolean;
  hidden: boolean;
  setEnabled(v: boolean): void;
  setIntervalMs(v: number): void;
  setOverride(v: number | null): void;
  refreshNow(): void;
}

const Ctx = createContext<RefreshState | null>(null);

export function useRefresh(): RefreshState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useRefresh outside RefreshProvider");
  return v;
}

export function RefreshProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [enabled, setEnabledState] = useState(true);
  const [intervalMs, setIntervalState] = useState(10000);
  const [override, setOverride] = useState<number | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [hidden, setHidden] = useState(false);
  const [refreshing, startTransition] = useTransition();

  // Per-viewer preference; storage can be unavailable (private mode), so never required.
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
      if (saved && typeof saved.enabled === "boolean") setEnabledState(saved.enabled);
      if (saved && INTERVALS.includes(saved.intervalMs)) setIntervalState(saved.intervalMs);
    } catch {
      /* ignore */
    }
  }, []);
  const save = (next: { enabled: boolean; intervalMs: number }) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };
  const setEnabled = (v: boolean) => {
    setEnabledState(v);
    save({ enabled: v, intervalMs });
  };
  const setIntervalMs = (v: number) => {
    setIntervalState(v);
    save({ enabled, intervalMs: v });
  };

  const refreshNow = useCallback(() => startTransition(() => router.refresh()), [router]);

  // A navigation renders fresh server data too.
  useEffect(() => setLastUpdated(Date.now()), [pathname]);

  // "Last updated" = when a refresh finished.
  const wasRefreshing = useRef(false);
  useEffect(() => {
    if (wasRefreshing.current && !refreshing) setLastUpdated(Date.now());
    wasRefreshing.current = refreshing;
  }, [refreshing]);

  useEffect(() => {
    const onVisibility = () => {
      setHidden(document.hidden);
      if (!document.hidden && enabled) refreshNow(); // catch up after being hidden
    };
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [enabled, refreshNow]);

  const effectiveMs = override ?? intervalMs;
  useEffect(() => {
    if (!enabled || hidden) return;
    const id = window.setInterval(refreshNow, effectiveMs);
    return () => window.clearInterval(id);
  }, [enabled, hidden, effectiveMs, refreshNow]);

  return (
    <Ctx.Provider
      value={{ enabled, intervalMs, override, effectiveMs, lastUpdated, refreshing, hidden,
               setEnabled, setIntervalMs, setOverride, refreshNow }}
    >
      {children}
    </Ctx.Provider>
  );
}

/** Rendered by a page that needs its own refresh rate while it is open. */
export function RefreshRate({ ms }: { ms: number }) {
  const { setOverride } = useRefresh();
  useEffect(() => {
    setOverride(ms);
    return () => setOverride(null);
  }, [ms, setOverride]);
  return null;
}
