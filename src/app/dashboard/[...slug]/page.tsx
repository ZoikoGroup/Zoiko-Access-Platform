import { Construction } from "lucide-react";
import Link from "next/link";
import { findNavLabel } from "@/lib/nav";

export default async function SectionPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const path = `/dashboard/${slug.join("/")}`;
  const title = findNavLabel(path) ?? slug[slug.length - 1].replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <div className="mx-auto max-w-[1500px]">
      <div className="border-b border-neu-dark/70 pb-6 pt-2 animate-fade-up">
        <p className="text-sm font-medium text-slate-400">Dashboard / {slug.map((s) => s.replace(/-/g, " ")).join(" / ")}</p>
        <h1 className="mt-1 text-4xl font-bold tracking-tight text-navy-900">{title}</h1>
      </div>
      <div className="neu-card mt-8 grid place-items-center px-6 py-20 text-center animate-fade-up [animation-delay:0.1s]">
        <span className="grid size-20 place-items-center rounded-full bg-neu shadow-neu-in">
          <Construction className="size-9 text-brand animate-float" />
        </span>
        <h2 className="mt-6 text-2xl font-bold text-navy-900">{title} is on its way</h2>
        <p className="mt-2 max-w-md text-slate-500">This section is wired into navigation and ready for its content.</p>
        <Link href="/dashboard" className="neu-btn mt-6 rounded-xl px-5 py-2.5 text-sm font-semibold text-brand">Back to dashboard</Link>
      </div>
    </div>
  );
}
