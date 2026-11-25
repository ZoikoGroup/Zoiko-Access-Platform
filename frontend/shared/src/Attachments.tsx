import { useState } from "react";

/** A file already attached to a message: what the server tells us (never its content). */
export interface AttachmentRef { id: string; name: string; content_type: string; size: number }
/** A file about to be sent: its name and its bytes as base64. */
export interface AttachmentPayload { name: string; data: string }

export const MAX_FILES = 3;
export const MAX_BYTES = 1_000_000;
/** The kinds the server accepts. It checks the file's own bytes too, so this is only a convenience. */
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.txt";

export function sizeText(bytes: number): string {
  return bytes < 1000 ? `${bytes} bytes` : bytes < 1_000_000 ? `${Math.round(bytes / 1000)} KB` : `${(bytes / 1_000_000).toFixed(1)} MB`;
}

async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Turn the chosen files into what the API takes. */
export async function toPayloads(files: File[]): Promise<AttachmentPayload[]> {
  return Promise.all(files.map(async (f) => ({ name: f.name, data: await toBase64(f) })));
}

/** Pick up to three small files (a PDF, a picture or a text file, each up to 1 MB) to send with a message. */
export function AttachPicker({ files, onChange, disabled }: { files: File[]; onChange: (files: File[]) => void; disabled?: boolean }) {
  const [problem, setProblem] = useState("");

  function add(list: FileList | null) {
    setProblem("");
    if (!list) return;
    const next = [...files, ...Array.from(list)];
    if (next.length > MAX_FILES) { setProblem(`At most ${MAX_FILES} files can go with one message.`); return; }
    const big = next.find((f) => f.size > MAX_BYTES);
    if (big) { setProblem(`${big.name} is too big. Each file can be up to ${sizeText(MAX_BYTES)}.`); return; }
    if (next.some((f) => f.size === 0)) { setProblem("A file with nothing in it cannot be sent."); return; }
    onChange(next);
  }

  return (
    <div>
      <label>Attach files (optional)
        <input type="file" accept={ACCEPT} multiple disabled={disabled || files.length >= MAX_FILES}
          aria-label="Attach files" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </label>
      <small style={{ display: "block" }}>PDF, PNG, JPEG or text; up to {MAX_FILES} files of {sizeText(MAX_BYTES)} each. Files are not checked for viruses, so only open files from people you trust.</small>
      {files.map((f, i) => (
        <div key={`${f.name}-${i}`}>
          {f.name} <small>({sizeText(f.size)})</small>{" "}
          <button type="button" className="btn ghost" onClick={() => onChange(files.filter((_, j) => j !== i))}>
            Remove<span className="sr-only"> {f.name}</span>
          </button>
        </div>
      ))}
      {problem && <div className="error" role="alert">{problem}</div>}
    </div>
  );
}

/** The files attached to one message, each a button that downloads it. */
export function AttachmentLinks({ items, onOpen }: { items?: AttachmentRef[]; onOpen: (a: AttachmentRef) => Promise<void> }) {
  const [error, setError] = useState("");
  if (!items || items.length === 0) return null;
  return (
    <div>
      {items.map((a) => (
        <div key={a.id}>
          <button type="button" className="btn ghost"
            onClick={() => { setError(""); onOpen(a).catch((e) => setError(e instanceof Error ? e.message : "Could not download")); }}>
            Download {a.name} <small>({sizeText(a.size)})</small>
          </button>
        </div>
      ))}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
