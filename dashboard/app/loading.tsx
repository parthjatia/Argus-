import { PanelSkeleton, TileSkeleton } from "@/components/ui";

// Shown while a page's server data loads on navigation (each panel also has its own state).
export default function Loading() {
  return (
    <div className="grid gap-2" aria-busy aria-label="Loading">
      <div className="h-8" />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        {["", " ", "  ", "   ", "    ", "     "].map((k) => (
          <TileSkeleton key={k} label="loading" />
        ))}
      </div>
      <PanelSkeleton title="Loading" rows={10} />
    </div>
  );
}
