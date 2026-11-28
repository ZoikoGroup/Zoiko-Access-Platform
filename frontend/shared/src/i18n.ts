/**
 * The translation framework (decided 27 Nov 2026, DEV-402: English only for now, with the framework
 * built so more languages can be added).
 *
 *   t("auth.otherAccount", { name: "North Clinic" })      wording in the chosen language
 *   formatDate(iso) / formatNumber(n)                      in the chosen language's style
 *
 * Adding a language: write `locales/<code>.ts` as a `Partial<Record<MessageKey, string>>`, add it to
 * CATALOGUES, and list it in AVAILABLE. A key it does not have falls back to English, never to a
 * blank. Health wording needs an approved translator (and, for messages, template approval).
 * The chosen language is remembered in this browser only; nothing is sent to the server.
 */
import { en, type MessageKey } from "./locales/en";

export type Language = "en";
export const AVAILABLE: readonly { code: Language; name: string }[] = [{ code: "en", name: "English" }];
const CATALOGUES: Record<Language, Partial<Record<MessageKey, string>>> = { en };
const STORAGE_KEY = "zoiko.language";

let current: Language = "en";

function isAvailable(code: string): code is Language {
  return AVAILABLE.some((l) => l.code === code);
}

/** The first of the browser's languages we have, else English. "en-GB" matches "en". */
export function pickLanguage(preferred: readonly string[]): Language {
  for (const tag of preferred) {
    const base = tag.toLowerCase().split("-")[0];
    if (isAvailable(base)) return base;
  }
  return "en";
}

export function language(): Language {
  return current;
}

export function setLanguage(code: string): Language {
  current = isAvailable(code) ? code : "en";
  try { localStorage.setItem(STORAGE_KEY, current); } catch { /* storage may be blocked */ }
  if (typeof document !== "undefined") document.documentElement.lang = current;
  return current;
}

/** Called once as an app starts: the remembered choice, else the browser's, else English. */
export function startLanguage(): Language {
  let saved = "";
  try { saved = localStorage.getItem(STORAGE_KEY) ?? ""; } catch { /* storage may be blocked */ }
  const nav = typeof navigator !== "undefined" ? navigator.languages ?? [navigator.language] : [];
  return setLanguage(saved && isAvailable(saved) ? saved : pickLanguage(nav));
}

export function t(key: MessageKey, vars?: Record<string, string | number>): string {
  const text = CATALOGUES[current][key] ?? en[key];
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole);
}

export function formatDate(iso: string, options?: Intl.DateTimeFormatOptions): string {
  return new Date(iso).toLocaleString(current, options ?? { day: "numeric", month: "short", year: "numeric" });
}

export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(current, options).format(value);
}
