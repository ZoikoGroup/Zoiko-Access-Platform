/** Full-screen branded loader: the ZA mark inside a pulsing neumorphic ring. */
export default function BrandLoader({ label = "Loading" }: { label?: string }) {
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-neu/90 backdrop-blur-sm animate-fade-up">
      <div className="flex flex-col items-center gap-6">
        <div className="relative grid size-28 place-items-center">
          <span className="absolute inset-0 rounded-full bg-brand/25 animate-pulse-ring" />
          <span className="absolute inset-0 rounded-full bg-sky/20 animate-pulse-ring [animation-delay:0.6s]" />
          <div className="relative grid size-28 place-items-center rounded-full bg-neu shadow-neu">
            <svg className="absolute inset-2 animate-spin-slow" viewBox="0 0 100 100" aria-hidden>
              <defs>
                <linearGradient id="za-ring" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#6A3CF0" />
                  <stop offset="100%" stopColor="#4F8BFF" />
                </linearGradient>
              </defs>
              <circle cx="50" cy="50" r="44" fill="none" stroke="url(#za-ring)" strokeWidth="6"
                strokeLinecap="round" strokeDasharray="200 80" />
            </svg>
            <div className="grid size-14 place-items-center rounded-full bg-navy shadow-glow">
              <span className="text-lg font-black italic tracking-tighter text-white">
                Z<span className="text-brand-300">A</span>
              </span>
            </div>
          </div>
        </div>
        <p className="flex items-center gap-1 text-sm font-semibold tracking-[0.3em] text-navy uppercase">
          {label}
          <span className="inline-flex gap-0.5">
            {[0, 1, 2].map((i) => (
              <span key={i} className="size-1 animate-bounce rounded-full bg-brand"
                style={{ animationDelay: `${i * 0.15}s` }} />
            ))}
          </span>
        </p>
      </div>
    </div>
  );
}
