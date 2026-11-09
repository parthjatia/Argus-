import assert from "node:assert/strict";
import { test } from "node:test";
import { buildTree } from "./tree.ts";

const s = (id: string, parent: string | null, t: number) => ({
  span_id: id,
  parent_span_id: parent,
  start_time: `2026-11-10T10:00:0${t}.000Z`,
});

const shape = (rows: ReturnType<typeof buildTree>) =>
  rows.map((r) => `${"  ".repeat(r.depth)}${r.span.span_id}${r.orphan ? " (orphan)" : ""}`);

test("nests children under parents, siblings by start time", () => {
  // API order is by start time, but children may come before parents in any order.
  const rows = buildTree([s("b", "root", 2), s("a", "root", 1), s("a1", "a", 1), s("root", null, 0)]);
  assert.deepEqual(shape(rows), ["root", "  a", "    a1", "  b"]);
});

test("a child whose parent hasn't arrived is shown at top level as an orphan", () => {
  const rows = buildTree([s("child", "missing-parent", 1), s("other-root", null, 0)]);
  assert.deepEqual(shape(rows), ["other-root", "child (orphan)"]);
});

test("the orphan's own children still nest under it", () => {
  const rows = buildTree([s("c", "missing", 1), s("c1", "c", 2)]);
  assert.deepEqual(shape(rows), ["c (orphan)", "  c1"]);
});

test("parent cycles don't loop forever and every span is shown once", () => {
  const rows = buildTree([s("x", "y", 1), s("y", "x", 2)]);
  assert.equal(rows.length, 2);
  assert.ok(rows.every((r) => r.orphan));
});

test("empty input", () => {
  assert.deepEqual(buildTree([]), []);
});
