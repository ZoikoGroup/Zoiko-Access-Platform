/** Items whose label starts with, or contains, what was typed: prefix matches first, then the rest
 *  in their original order, at most five. Nothing typed gives nothing. */
export function matchPages<T extends { label: string }>(items: T[], text: string): T[] {
  const word = text.trim().toLowerCase();
  if (!word) return [];
  const starts = items.filter((n) => n.label.toLowerCase().startsWith(word));
  const inside = items.filter((n) => !starts.includes(n) && n.label.toLowerCase().includes(word));
  return [...starts, ...inside].slice(0, 5);
}
