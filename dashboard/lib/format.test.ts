import assert from "node:assert/strict";
import { test } from "node:test";
import { formatCost, formatDuration, formatRelative, formatTokens, rangeStart } from "./format.ts";

test("formatDuration", () => {
  assert.equal(formatDuration(0), "0 ms");
  assert.equal(formatDuration(0.4), "<1 ms");
  assert.equal(formatDuration(245.6), "246 ms");
  assert.equal(formatDuration(119_600), "2m 0s"); // no "1m 60s"
  assert.equal(formatDuration(1240), "1.24 s");
  assert.equal(formatDuration(12_400), "12.4 s");
  assert.equal(formatDuration(125_000), "2m 5s");
  assert.equal(formatDuration(null), "–");
});

test("formatCost distinguishes unknown from free", () => {
  assert.equal(formatCost(null), "–");
  assert.equal(formatCost(0), "$0");
  assert.equal(formatCost(0.002026), "$0.0020");
  assert.equal(formatCost(0.00001), "<$0.0001");
  assert.equal(formatCost(12.345), "$12.35");
});

test("formatTokens", () => {
  assert.equal(formatTokens(950), "950");
  assert.equal(formatTokens(2068), "2.1k");
  assert.equal(formatTokens(45_000), "45k");
  assert.equal(formatTokens(3_200_000), "3.2M");
});

test("formatRelative", () => {
  const now = Date.parse("2026-11-10T12:00:00Z");
  assert.equal(formatRelative("2026-11-10T11:59:58Z", now), "just now");
  assert.equal(formatRelative("2026-11-10T11:57:00Z", now), "3 min ago");
  assert.equal(formatRelative("2026-11-09T12:00:00Z", now), "1 d ago");
});

test("rangeStart", () => {
  const now = Date.parse("2026-11-10T12:00:00Z");
  assert.equal(rangeStart("1h", now), "2026-11-10T11:00:00.000Z");
  assert.equal(rangeStart("all", now), undefined);
  assert.equal(rangeStart("bogus", now), undefined);
});
