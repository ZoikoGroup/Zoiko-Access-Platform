import Image from "next/image";
import { Users, ShieldCheck, FileText, ChartColumn, HeartPulse, CalendarCheck } from "lucide-react";
import Logo from "@/components/ui/Logo";

/*
 * The login hero is split into pieces so the page grid can arrange them per breakpoint:
 *   mobile/tablet → HeroIntro, form, HeroDetails stacked (each with its own purple background)
 *   desktop       → HeroBackdrop spans the left column behind HeroIntro + HeroDetails
 */

const features = [
  { icon: Users, title: "Connected healthcare", sub: "People • Providers • Services" },
  { icon: ShieldCheck, title: "Trusted and compliant", sub: "Privacy • Security • Governance" },
  { icon: FileText, title: "Simpler access", sub: "Find, book and manage with ease" },
  { icon: ChartColumn, title: "Better outcomes", sub: "Insights for healthier lives" },
];

// Positions are percentages of the doctor photo (chip centres) so the crisp chips sit over the soft ones baked into it.
const chips = [
  { icon: HeartPulse, title: "Care", sub: "Made easier", pos: "top-[17%] left-[27%]", delay: "0s" },
  { icon: CalendarCheck, title: "Appointments", sub: "Simplified", pos: "top-[30.5%] left-[34%]", delay: "0.8s" },
  { icon: ShieldCheck, title: "Health", sub: "On your terms", pos: "top-[43.5%] left-[35%]", delay: "1.6s" },
];

const purpleBg =
  "bg-[radial-gradient(120%_80%_at_0%_0%,#2a1a8f_0%,transparent_60%),radial-gradient(90%_70%_at_100%_100%,#4f8bff_0%,transparent_55%),linear-gradient(135deg,#1c1068_0%,#3a1fb3_45%,#6a3cf0_80%)]";

function Glows() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className={`absolute inset-0 ${purpleBg}`} />
      <div className="absolute -right-24 top-8 size-[520px] max-w-full rounded-full bg-brand-400/40 blur-3xl" />
      <div className="absolute bottom-[-10%] left-[30%] size-[420px] max-w-full rounded-full bg-sky/30 blur-3xl" />
    </div>
  );
}

function Doctor({ className }: { className: string }) {
  return (
    <div className={`pointer-events-none absolute aspect-[455/724] ${className}`}>
      <Image src="/images/hero-doctor.png" alt="Doctor reviewing a patient's records on a tablet" fill priority
        sizes="(min-width: 1024px) 26vw, 40vw" className="object-contain object-top [mask-image:linear-gradient(to_bottom,#000_60%,transparent_80%)] animate-fade-up [animation-delay:0.15s]" />
      {chips.map(({ icon: Icon, title, sub, pos, delay }) => (
        <div key={title} className={`absolute ${pos} min-w-[36%] -translate-y-1/2`}>
        <div style={{ animationDelay: delay }}
          className={`pointer-events-auto flex w-max min-w-full items-center gap-2 rounded-xl border border-white/25 bg-white/15 p-1.5 pr-3 shadow-lg backdrop-blur-md animate-float-sm transition duration-300 hover:scale-105 hover:bg-white/25 sm:gap-2.5 sm:p-2`}>
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-brand-400 to-brand shadow-glow sm:size-8 xl:size-9">
            <Icon className="size-3.5 sm:size-4" />
          </span>
          <span className="whitespace-nowrap leading-tight">
            <span className="block text-[11px] font-semibold sm:text-xs xl:text-sm">{title}</span>
            <span className="block text-[10px] text-white/75 sm:text-[11px] xl:text-xs">{sub}</span>
          </span>
        </div>
        </div>
      ))}
    </div>
  );
}

/** Desktop only: one continuous purple panel (with the doctor) behind the whole left column. */
export function HeroBackdrop() {
  return (
    <div className="relative hidden overflow-hidden lg:col-start-1 lg:row-span-2 lg:row-start-1 lg:block">
      <Glows />
      <Doctor className="right-0 top-0 w-[44%] max-w-[480px]" />
    </div>
  );
}

export function HeroIntro() {
  return (
    <section className="relative overflow-hidden text-white lg:col-start-1 lg:row-start-1 lg:overflow-visible lg:pointer-events-none">
      <div className="lg:hidden"><Glows /></div>
      <Doctor className="right-2 top-2 hidden h-full sm:block lg:hidden" />

      <div className="relative px-5 pb-10 pt-8 sm:max-w-[60%] sm:px-8 sm:pb-12 lg:max-w-none lg:px-12 lg:pb-0 lg:pt-10 xl:px-14">
        <div className="lg:pointer-events-auto lg:w-fit animate-fade-up">
          <Logo variant="white" className="h-12 w-auto sm:h-14 xl:h-16" priority />
        </div>
        <h1 className="mt-7 text-[2rem] font-bold leading-[1.15] tracking-tight animate-fade-up [animation-delay:0.1s] sm:mt-9 sm:text-[2.4rem] lg:text-[clamp(2rem,3.1vw,3.25rem)]">
          Your gateway to
          <br />a{" "}
          <span className="bg-gradient-to-r from-[#a98bff] to-[#c4b2ff] bg-clip-text text-transparent">healthier, smarter</span>
          <br />
          tomorrow.
        </h1>
        <p className="mt-4 max-w-md text-sm leading-relaxed text-white/80 animate-fade-up [animation-delay:0.2s] sm:text-[15px] lg:mt-5">
          Access care, services and insights in one secure platform — for individuals, families and organisations.
        </p>
      </div>
    </section>
  );
}

export function HeroDetails() {
  return (
    <section className="relative flex flex-col overflow-hidden text-white lg:col-start-1 lg:row-start-2 lg:overflow-visible">
      <div className="lg:hidden"><Glows /></div>

      <div className="relative flex flex-1 flex-col px-5 pt-10 sm:px-8 lg:px-12 lg:pt-7 xl:px-14">
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1 lg:gap-3.5">
          {features.map(({ icon: Icon, title, sub }, i) => (
            <li key={title} className="group flex items-center gap-4 animate-fade-up" style={{ animationDelay: `${0.3 + i * 0.08}s` }}>
              <span className="grid size-11 shrink-0 place-items-center rounded-full border border-white/30 bg-white/10 transition duration-300 group-hover:scale-110 group-hover:bg-white/20">
                <Icon className="size-5" />
              </span>
              <span className="leading-tight">
                <span className="block font-semibold">{title}</span>
                <span className="block text-sm text-white/70">{sub}</span>
              </span>
            </li>
          ))}
        </ul>

        {/* Devices: the laptop overlaps the phone slightly, as in the reference */}
        <div className="relative mx-auto mt-10 flex w-full max-w-[640px] items-end lg:mx-0 lg:mt-auto lg:max-w-[600px] lg:pt-6 xl:max-w-[700px]">
          <div className="relative z-10 w-[32%] shrink-0 transition duration-500 hover:-translate-y-2 hover:-rotate-2 animate-fade-up [animation-delay:0.5s]">
            <Image src="/images/hero-phone.png" alt="Zoiko Access mobile app" width={300} height={384}
              sizes="(min-width: 1024px) 14vw, 30vw" className="h-auto w-full" />
          </div>
          <div className="-ml-[6%] w-[74%] shrink-0 transition duration-500 hover:-translate-y-2 animate-fade-up [animation-delay:0.6s]">
            <Image src="/images/hero-laptop.png" alt="Zoiko Access web dashboard" width={628} height={470}
              sizes="(min-width: 1024px) 30vw, 70vw" className="h-auto w-full" />
          </div>
        </div>
      </div>

      <nav className="relative flex flex-wrap items-center justify-center gap-x-3 gap-y-2 px-5 pb-6 pt-4 text-[10px] font-medium tracking-[0.2em] text-white/80 sm:px-8 sm:text-[11px] sm:tracking-[0.25em] lg:justify-start lg:px-12 xl:px-14">
        <span className="border-b-2 border-white pb-1 text-white">PEOPLE</span>
        {["CARE", "SERVICES", "INSIGHTS", "A HEALTHIER TOMORROW"].map((t) => (
          <span key={t} className="flex items-center gap-3">
            <span className="text-white/40">/</span>
            <span className="pb-1">{t}</span>
          </span>
        ))}
      </nav>
    </section>
  );
}
