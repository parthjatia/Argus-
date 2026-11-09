import Link from "next/link";

export default function NotFound() {
  return (
    <div className="py-20 text-center">
      <p className="text-lg font-medium">Not found</p>
      <p className="mt-1 text-sm text-ink-2">
        This trace doesn&apos;t exist in your project (or hasn&apos;t been written yet; give the worker a second).
      </p>
      <Link href="/traces" className="mt-4 inline-block text-sm font-medium text-accent">
        ← Back to traces
      </Link>
    </div>
  );
}
