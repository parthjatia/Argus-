import { Suspense } from "react";
import { api } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import type { Dependency, SystemStatus } from "@/lib/types";
import { Id } from "@/components/copy";
import { Legend, TimeBars } from "@/components/charts";
import { Empty, PageHeader, Panel, PanelError, PanelSkeleton, Td, Th, Tile, TileSkeleton } from "@/components/ui";

export const dynamic = "force-dynamic";

// Thresholds for the amber "warning" state.
const BACKLOG_WARN = 10_000;
const PENDING_AGE_WARN_MS = 60_000; // = the worker's XAUTOCLAIM idle time
const CONSUMER_IDLE_WARN_MS = 30_000;

export default function SystemPage() {
  return (
    <>
      <PageHeader title="System">
        <span>Pipeline health: API → Redis Stream &quot;spans&quot; → worker group &quot;writers&quot; → Postgres.</span>
      </PageHeader>
      <Suspense fallback={<SystemSkeleton />}>
        <SystemBody />
      </Suspense>
    </>
  );
}

const TILES = ["Postgres", "Redis", "Backlog", "Pending (unacked)", "Dead letters", "Ingest / min", "Writes / min"];

function SystemSkeleton() {
  return (
    <div className="grid gap-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        {TILES.map((l) => (
          <TileSkeleton key={l} label={l} />
        ))}
      </div>
      <PanelSkeleton title="Throughput per minute" rows={6} />
    </div>
  );
}

function dep(d: Dependency) {
  return d.ok ? (
    <span>
      <span aria-hidden>✓ </span>up
    </span>
  ) : (
    <span>
      <span aria-hidden>✕ </span>down
    </span>
  );
}

async function SystemBody() {
  let s: SystemStatus;
  try {
    s = await api.system(30);
  } catch (error) {
    return (
      <Panel title="System status">
        <PanelError error={error} />
      </Panel>
    );
  }
  const st = s.stream;
  // The current minute is still filling up: rates use the last COMPLETE minute.
  const last = s.rates.length >= 2 ? s.rates[s.rates.length - 2] : s.rates.at(-1);
  const backlogWarn = st !== null && st.backlog !== null && st.backlog >= BACKLOG_WARN;
  const pendingWarn = st !== null && st.oldest_pending_ms !== null && st.oldest_pending_ms >= PENDING_AGE_WARN_MS;
  const ingested = s.rates.reduce((a, r) => a + r.ingested, 0);
  const written = s.rates.reduce((a, r) => a + r.written, 0);

  return (
    <div className="grid gap-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
        <Tile label="Postgres" value={dep(s.postgres)} tone={s.postgres.ok ? "normal" : "critical"} hint={s.postgres.ok ? `${s.postgres.latency_ms} ms round trip` : s.postgres.error} />
        <Tile label="Redis" value={dep(s.redis)} tone={s.redis.ok ? "normal" : "critical"} hint={s.redis.ok ? `${s.redis.latency_ms} ms round trip` : s.redis.error} />
        <Tile
          label="Backlog"
          value={st ? (st.backlog ?? "?").toLocaleString() : "–"}
          tone={backlogWarn ? "warning" : "normal"}
          hint="not yet read by a worker"
        />
        <Tile
          label="Pending (unacked)"
          value={st ? st.pending.toLocaleString("en") : "–"}
          tone={pendingWarn ? "warning" : "normal"}
          hint={st?.oldest_pending_ms != null ? `oldest ${formatDuration(st.oldest_pending_ms)}` : "read, not yet committed"}
        />
        <Tile label="Dead letters" value={st ? st.dead_letters : "–"} tone={st && st.dead_letters ? "critical" : "normal"} hint="stream spans:dead" />
        <Tile label="Ingest / min" value={last ? last.ingested.toLocaleString("en") : "–"} hint="spans accepted (last full min)" />
        <Tile
          label="Writes / min"
          value={last ? last.written.toLocaleString("en") : "–"}
          hint="new rows in Postgres (last full min)"
        />
      </div>

      <div className="grid gap-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel
          title="Throughput per minute · last 30 min"
          right={
            <Legend
              items={[
                { label: `ingested ${ingested.toLocaleString("en")}`, color: "var(--accent)" },
                { label: `written ${written.toLocaleString("en")}`, color: "var(--series-2)" },
              ]}
            />
          }
        >
          <div className="p-2">
            {!s.redis.ok ? (
              <Empty>Redis is unreachable: no throughput counters.</Empty>
            ) : ingested + written === 0 ? (
              <Empty>No spans ingested in the last 30 minutes.</Empty>
            ) : (
              <TimeBars
                rows={s.rates.map((r) => ({ bucket: r.minute, ingested: r.ingested, written: r.written }))}
                series={[
                  { key: "ingested", label: "ingested", color: "var(--accent)" },
                  { key: "written", label: "written", color: "var(--series-2)" },
                ]}
                stacked={false}
                total={false}
                bucketS={60}
                format="count"
                height={200}
              />
            )}
            <p className="mt-1 text-[11px] text-ink-3">
              Written can be lower than ingested: duplicates (retries) are not inserted twice. A growing gap with a
              growing backlog means the worker can&apos;t keep up.
            </p>
          </div>
        </Panel>

        <div className="grid content-start gap-2">
          <Panel title="Workers (consumer group writers)" right={<span className="num">{st?.consumers.length ?? 0}</span>}>
            {!st ? (
              <Empty>Redis is unreachable.</Empty>
            ) : st.consumers.length === 0 ? (
              <Empty>No worker has joined the group yet. Is the worker service running?</Empty>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <Th>Consumer</Th>
                    <Th num>Pending</Th>
                    <Th num>Idle</Th>
                    <Th>State</Th>
                  </tr>
                </thead>
                <tbody>
                  {st.consumers.map((c) => {
                    const stale = c.idle_ms >= CONSUMER_IDLE_WARN_MS;
                    return (
                      <tr key={c.name} className="border-b border-border last:border-0">
                        <Td>
                          <Id value={c.name} />
                        </Td>
                        <Td num>{c.pending}</Td>
                        <Td num className={stale ? "text-warning" : ""}>
                          {formatDuration(c.idle_ms)}
                        </Td>
                        <Td className={stale ? "text-warning" : "text-ink-2"}>
                          {stale ? (c.pending ? "⚠ stuck? (reclaimed after 60 s)" : "⚠ idle / gone") : "reading"}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Stream">
            {!st ? (
              <Empty>Redis is unreachable.</Empty>
            ) : (
              <dl className="grid grid-cols-[10rem_1fr] text-xs">
                {[
                  ["Stream length", st.length.toLocaleString("en"), "kept up to MAXLEN ~1M, also already stored"],
                  ["Backlog (lag)", st.backlog === null ? "unknown" : st.backlog.toLocaleString("en"), "never delivered"],
                  ["Pending", st.pending.toLocaleString("en"), "delivered, waiting for COMMIT + XACK"],
                  ["Oldest pending", st.oldest_pending_ms === null ? "–" : formatDuration(st.oldest_pending_ms), "XAUTOCLAIM after 60 s"],
                  ["Dead letters", String(st.dead_letters), "need manual inspection"],
                  ["Checked at", s.checked_at.slice(11, 19) + " UTC", ""],
                ].map(([k, v, note]) => (
                  <div key={k} className="contents">
                    <dt className="border-b border-border bg-surface-2 px-2 py-1 text-[11px] text-ink-3">{k}</dt>
                    <dd className="border-b border-border px-2 py-1">
                      <span className="num">{v}</span>
                      {note && <span className="ml-2 text-[11px] text-ink-3">{note}</span>}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
