/** Neumorphic skeleton shown while a dashboard route streams in. */
export default function Loading() {
  return (
    <div className="mx-auto max-w-[1500px] pt-2" aria-busy="true" aria-label="Loading">
      <div className="border-b border-neu-dark/70 pb-6">
        <div className="skeleton h-12 w-64" />
        <div className="skeleton mt-3 h-4 w-96 max-w-full" />
      </div>
      <div className="mt-8 grid gap-6 sm:grid-cols-2 2xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="neu-card p-6">
            <div className="skeleton h-4 w-32" />
            <div className="mt-5 flex items-center gap-4">
              <div className="skeleton size-14 rounded-full" />
              <div className="skeleton h-9 w-28" />
            </div>
            <div className="skeleton mt-5 h-3 w-48" />
          </div>
        ))}
      </div>
      <div className="mt-8 grid gap-6 xl:grid-cols-3">
        <div className="neu-card p-7 xl:col-span-2">
          <div className="skeleton h-5 w-48" />
          <div className="skeleton mt-4 h-12 w-36" />
          <div className="mt-8 flex h-52 items-end gap-1.5">
            {Array.from({ length: 28 }).map((_, i) => (
              <div key={i} className="skeleton flex-1 rounded-b-none" style={{ height: `${35 + ((i * 37) % 60)}%` }} />
            ))}
          </div>
        </div>
        <div className="neu-card space-y-5 p-7">
          <div className="skeleton h-5 w-40" />
          {Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton h-8" />)}
        </div>
      </div>
    </div>
  );
}
