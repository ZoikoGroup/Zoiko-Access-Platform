// Slot times arrive as the clinic's wall-clock ("2027-06-15T10:00:00") plus its zone name.
// We show that wall-clock as written, never converted to the browser's zone.

function parts(local: string): Date {
  const [d, t = "00:00:00"] = local.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min] = t.split(":").map(Number);
  return new Date(y, m - 1, day, h, min);
}

export const dayKey = (local: string): string => local.slice(0, 10);

export function dayLabel(local: string): string {
  return parts(local).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

export function timeLabel(local: string): string {
  return parts(local).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function whenLabel(local: string, zone: string): string {
  return `${dayLabel(local)} at ${timeLabel(local)} (${zone})`;
}

export function dateBlock(local: string): { month: string; day: string; weekday: string } {
  const d = parts(local);
  return {
    month: d.toLocaleDateString(undefined, { month: "short" }),
    day: String(d.getDate()),
    weekday: d.toLocaleDateString(undefined, { weekday: "short" }),
  };
}

export const displayName = (subject: string): string => {
  const local = subject.split("@")[0].replace(/[._-]+/g, " ").trim();
  return local ? local.replace(/\b\w/g, (c) => c.toUpperCase()) : subject;
};
