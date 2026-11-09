// Categorical colours for models.
//
// Rules (dataviz): fixed slot order, never cycled; colour follows the entity, not its rank.
// So the slot comes from a STABLE list (all models seen in the last 90 days, sorted),
// not from whatever happens to be in the current filter.

// Four slots (accent + three greys, per the design rules); more models fold into "Other".
export const MAX_SERIES = 4;
export const OTHER = "Other";

export function modelColors(allModels: string[]): Map<string, string> {
  const sorted = [...new Set(allModels)].sort();
  const map = new Map<string, string>();
  sorted.forEach((m, i) => map.set(m, i < MAX_SERIES ? `var(--series-${i + 1})` : "var(--series-other)"));
  map.set(OTHER, "var(--series-other)");
  return map;
}

/** Name to plot a model under: itself if it has a slot, otherwise "Other". */
export function seriesName(model: string, colors: Map<string, string>): string {
  return colors.get(model) && colors.get(model) !== "var(--series-other)" ? model : OTHER;
}
