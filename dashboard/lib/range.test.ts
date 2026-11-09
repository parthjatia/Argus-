import { test } from "node:test";
import assert from "node:assert/strict";
import { bucketLabel, bucketStarts, parseRange, rangeWindow, withRange } from "./range.ts";

test("parseRange accepts only known ranges", () => {
  assert.equal(parseRange("15m"), "15m");
  assert.equal(parseRange(["7d", "1h"]), "7d");
  assert.equal(parseRange("30d"), "1h");
  assert.equal(parseRange(undefined), "1h");
});

test("rangeWindow gives an absolute window and a bucket size", () => {
  const now = Date.UTC(2026, 10, 9, 12, 0, 0);
  const w = rangeWindow("15m", now);
  assert.equal(w.end, "2026-11-09T12:00:00.000Z");
  assert.equal(w.start, "2026-11-09T11:45:00.000Z");
  assert.equal(w.bucketS, 30);
  assert.equal(rangeWindow("7d", now).bucketLabel, "day");
});

test("bucketStarts are epoch-aligned and cover the window", () => {
  const now = Date.UTC(2026, 10, 9, 12, 0, 45);
  const starts = bucketStarts(rangeWindow("15m", now));
  assert.equal(starts[0], "2026-11-09T11:45:30.000Z");
  assert.equal(starts.at(-1), "2026-11-09T12:00:30.000Z");
  assert.equal(starts.length, 31);
});

test("bucketLabel and withRange", () => {
  assert.equal(bucketLabel(120), "2 min");
  assert.equal(bucketLabel(3600), "hour");
  assert.equal(withRange("/live", "1h"), "/live");
  assert.equal(withRange("/traces?status=error", "24h"), "/traces?status=error&range=24h");
});
