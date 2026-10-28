import type { Metadata } from "next";
import { HeroBackdrop, HeroDetails, HeroIntro } from "@/components/auth/HeroPanel";
import AuthCard from "@/components/auth/AuthCard";

export const metadata: Metadata = { title: "Sign in · Zoiko Access" };

/*
 * Mobile/tablet: intro → form → details, stacked.
 * Desktop: two columns — hero (intro over details) on the left, form on the right.
 */
export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col overflow-x-hidden bg-[#f6f7fb] lg:grid lg:grid-cols-[57fr_43fr] lg:grid-rows-[auto_1fr]">
      <HeroBackdrop />
      <HeroIntro />
      <section className="relative z-10 -mt-5 flex justify-center rounded-t-3xl bg-[#f6f7fb] px-3 py-8 sm:px-8 sm:py-10 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:mt-0 lg:items-center lg:rounded-none lg:px-8 xl:px-12">
        <AuthCard />
      </section>
      <HeroDetails />
    </main>
  );
}
