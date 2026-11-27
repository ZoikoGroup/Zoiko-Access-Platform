import type { ResultValue } from "../api";

/** One row as typed; numbers are sent as numbers, anything else as a short text (DEV-330). */
export interface ValueRow { name: string; value: string; unit: string; low: string; high: string; flag: string }

export const emptyRow = (): ValueRow => ({ name: "", value: "", unit: "", low: "", high: "", flag: "" });

const num = (s: string) => (s.trim() !== "" && !Number.isNaN(Number(s)) ? Number(s) : null);

/** The rows that have a name and a value, in the shape the server accepts. */
export function toValues(rows: ValueRow[]): ResultValue[] {
  return rows
    .filter((r) => r.name.trim() && r.value.trim())
    .map((r) => ({
      name: r.name.trim(),
      value: num(r.value) ?? r.value.trim(),
      ...(r.unit.trim() ? { unit: r.unit.trim() } : {}),
      ...(num(r.low) !== null ? { low: num(r.low) } : {}),
      ...(num(r.high) !== null ? { high: num(r.high) } : {}),
      ...(r.flag ? { flag: r.flag } : {}),
    }));
}

/** Measured values of a result: name, value, unit, normal range and flag. Up to 50 rows. */
export function ValuesEditor({ rows, onChange, label }: { rows: ValueRow[]; onChange: (rows: ValueRow[]) => void; label: string }) {
  const set = (i: number, key: keyof ValueRow, v: string) => onChange(rows.map((r, n) => (n === i ? { ...r, [key]: v } : r)));
  return (
    <div role="group" aria-label={label} style={{ flexBasis: "100%" }}>
      <b>Measured values (optional)</b>
      <table style={{ width: "100%", fontSize: 13 }}>
        <thead><tr><th align="left">Test</th><th align="left">Value</th><th align="left">Unit</th><th align="left">Normal from</th><th align="left">to</th><th align="left">Flag</th><th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><input className="field" aria-label={`Test name ${i + 1}`} maxLength={80} value={r.name} onChange={(e) => set(i, "name", e.target.value)} /></td>
              <td><input className="field" aria-label={`Value ${i + 1}`} maxLength={40} value={r.value} onChange={(e) => set(i, "value", e.target.value)} style={{ width: 90 }} /></td>
              <td><input className="field" aria-label={`Unit ${i + 1}`} maxLength={20} value={r.unit} onChange={(e) => set(i, "unit", e.target.value)} style={{ width: 70 }} /></td>
              <td><input className="field" aria-label={`Normal from ${i + 1}`} value={r.low} onChange={(e) => set(i, "low", e.target.value)} style={{ width: 70 }} /></td>
              <td><input className="field" aria-label={`Normal to ${i + 1}`} value={r.high} onChange={(e) => set(i, "high", e.target.value)} style={{ width: 70 }} /></td>
              <td>
                <select aria-label={`Flag ${i + 1}`} value={r.flag} onChange={(e) => set(i, "flag", e.target.value)}>
                  <option value="">-</option><option value="normal">Normal</option><option value="high">High</option>
                  <option value="low">Low</option><option value="abnormal">Abnormal</option><option value="critical">Critical</option>
                </select>
              </td>
              <td><button className="btn ghost" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(rows.filter((_, n) => n !== i))}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length < 50 && <button className="btn ghost" onClick={() => onChange([...rows, emptyRow()])}>Add a value</button>}
    </div>
  );
}
