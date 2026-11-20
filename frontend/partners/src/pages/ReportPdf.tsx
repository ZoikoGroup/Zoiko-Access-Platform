import { useEffect, useState } from "react";
import { getAttachments, uploadReportPdf, type Attachment } from "../api";
import { Pill } from "../ui";

const SCAN: Record<Attachment["scan"], { text: string; kind: "ok" | "warn" | "bad" }> = {
  pending: { text: "waiting for the virus check", kind: "warn" },
  clean: { text: "checked", kind: "ok" },
  infected: { text: "virus found: removed", kind: "bad" },
};

/** Attach the lab's PDF report to a delivered result (DEV-345). Only the treating doctor opens it. */
export function ReportPdf({ requestId, token }: { requestId: string; token: string }) {
  const [files, setFiles] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { getAttachments(requestId).then(setFiles).catch(() => setFiles([])); }, [requestId]);
  async function send(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError("");
    try { const a = await uploadReportPdf(requestId, file); setFiles([...files, a]); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "Only a PDF of at most 5 MB, without scripts or embedded files."
        : code === "CONFLICT_VERSION" ? "No more files can be added to this result (at most 5)."
        : e instanceof Error ? e.message : "The upload did not work");
    } finally { setBusy(false); }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {files.map((f) => (
        <small key={f.id}>{f.filename} ({Math.ceil(f.size / 1024)} KB) <Pill kind={SCAN[f.scan].kind}>{SCAN[f.scan].text}</Pill></small>
      ))}
      {files.length < 5 && (
        <label>Attach the PDF report{" "}
          <input type="file" accept="application/pdf,.pdf" aria-label={`PDF report for ${token}`} disabled={busy}
            onChange={(e) => { void send(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      )}
      <small>Only the ordering clinic's treating doctor can open it, after a virus check.</small>
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
