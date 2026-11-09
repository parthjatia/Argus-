"use client";

import { useState } from "react";

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for non-secure contexts (e.g. the dashboard opened via a LAN IP over http).
    try {
      const el = document.createElement("textarea");
      el.value = text;
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.select();
      const ok = document.execCommand("copy");
      el.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

export function CopyButton({ value, label = "copy", className = "" }: { value: string; label?: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <button
      type="button"
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        setState((await copyText(value)) ? "copied" : "failed");
        window.setTimeout(() => setState("idle"), 1500);
      }}
      title={`Copy ${value}`}
      aria-label={`Copy ${value}`}
      className={`h-5 border border-border px-1 font-mono text-[10px] text-ink-3 hover:border-border-strong hover:text-ink ${className}`}
    >
      {state === "copied" ? "copied" : state === "failed" ? "failed" : label}
    </button>
  );
}

/** An ID in monospace with a copy button. `short` shows the first 8 characters. */
export function Id({ value, short = false }: { value: string; short?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      <span className="font-mono text-[11px] text-ink-3" title={value}>
        {short ? value.slice(0, 8) : value}
      </span>
      <CopyButton value={value} />
    </span>
  );
}
