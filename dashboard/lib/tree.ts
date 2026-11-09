// Build the span tree from the flat list the API returns.
//
// The server has no foreign key on parent_span_id (children may arrive before their
// parents), so this is where the tree is checked: a span whose parent is missing is
// shown at the top level and flagged as an orphan instead of being hidden.

export interface SpanLike {
  span_id: string;
  parent_span_id: string | null;
  start_time: string;
}

export interface TreeRow<T extends SpanLike> {
  span: T;
  depth: number;
  orphan: boolean; // has a parent_span_id that isn't in this trace (yet)
}

function byStart(a: SpanLike, b: SpanLike): number {
  return a.start_time < b.start_time ? -1 : a.start_time > b.start_time ? 1 :
    a.span_id < b.span_id ? -1 : 1;
}

/** Depth-first rows (parent before children, siblings by start time). */
export function buildTree<T extends SpanLike>(spans: T[]): TreeRow<T>[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string, T[]>();
  const tops: T[] = [];

  for (const s of spans) {
    const p = s.parent_span_id;
    if (p !== null && ids.has(p) && p !== s.span_id) {
      const list = children.get(p) ?? [];
      list.push(s);
      children.set(p, list);
    } else {
      tops.push(s);
    }
  }

  const rows: TreeRow<T>[] = [];
  const visited = new Set<string>();
  const visit = (s: T, depth: number) => {
    if (visited.has(s.span_id)) return; // defensive: never loop on bad data
    visited.add(s.span_id);
    const orphan = s.parent_span_id !== null && !ids.has(s.parent_span_id);
    rows.push({ span: s, depth, orphan });
    for (const c of (children.get(s.span_id) ?? []).sort(byStart)) visit(c, depth + 1);
  };
  for (const s of tops.sort(byStart)) visit(s, 0);

  // Spans in a parent cycle (a→b→a) are never reached from a top-level span: show them too.
  for (const s of spans) {
    if (!visited.has(s.span_id)) {
      visited.add(s.span_id);
      rows.push({ span: s, depth: 0, orphan: true });
    }
  }
  return rows;
}
