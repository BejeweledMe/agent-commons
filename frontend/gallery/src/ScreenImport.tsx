import { type FormEvent, type ReactElement, useRef, useState } from "react";
import { readBoundedJson } from "./contracts";
import { importMayHaveWritten, parseScreenImport, validScreenFile } from "./screenImportState";

type Props = {
  apiBase: string;
  enabled: boolean;
  text: (key: string) => string;
  onImported: (candidateId: string, title: string) => Promise<void>;
};
type ImportBody = { title: string; media_type: string; content_base64: string; idempotency_key: string };

async function encodeFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string" || !reader.result.includes(",")) reject(new Error("invalid_file"));
      else resolve(reader.result.slice(reader.result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error("unreadable_file"));
    reader.readAsDataURL(file);
  });
}

export function ScreenImport({ apiBase, enabled, text, onImported }: Props): ReactElement {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<"idle" | "pending" | "saved" | "error" | "invalid" | "saved_refresh_failed">("idle");
  const [uncertain, setUncertain] = useState(false);
  const intent = useRef<ImportBody | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  function reset(): void {
    intent.current = null;
    setFile(null);
    setTitle("");
    setState("idle");
    setUncertain(false);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!enabled || state === "pending") return;
    if (!intent.current && (!validScreenFile(file) || !title.trim())) {
      setState("invalid");
      return;
    }
    setState("pending");
    try {
      if (intent.current === null) {
        intent.current = { title: title.trim(), media_type: file!.type,
          content_base64: await encodeFile(file!), idempotency_key: crypto.randomUUID() };
      }
      const response = await fetch(`${apiBase}/gallery/import`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin", body: JSON.stringify(intent.current),
      });
      if (!response.ok) {
        const mayHaveWritten = importMayHaveWritten(response.status);
        setUncertain(mayHaveWritten);
        if (!mayHaveWritten) intent.current = null;
        setState("error");
        return;
      }
      const payload = parseScreenImport(await readBoundedJson(response, 4096));
      const importedTitle = intent.current.title;
      setUncertain(false);
      setState("saved");
      try { await onImported(payload.candidateId, importedTitle); }
      catch { setState("saved_refresh_failed"); }
    } catch {
      setUncertain(intent.current !== null);
      setState("error");
    }
  }

  const saved = state === "saved" || state === "saved_refresh_failed";
  const locked = state === "pending" || uncertain || saved;
  return <section className="screen-import" aria-labelledby="screen-import-title">
    <h3 id="screen-import-title">{text("import_title")}</h3>
    <p className="gallery-help">{text("import_help")}</p>
    <form onSubmit={(event) => void submit(event)}>
      <label htmlFor="screen-import-name">{text("import_name")}</label>
      <input id="screen-import-name" disabled={!enabled || locked} maxLength={256}
        value={title} onChange={(event) => { setTitle(event.target.value); intent.current = null; setState("idle"); }} required />
      <label htmlFor="screen-import-file">{text("import_file")}</label>
      <input id="screen-import-file" ref={fileInput} type="file" accept="image/png,image/jpeg"
        disabled={!enabled || locked} onChange={(event) => {
          setFile(event.target.files?.[0] ?? null); intent.current = null; setState("idle");
        }} required />
      {saved ? <button type="button" onClick={reset}>{text("import_another")}</button>
        : <button type="submit" disabled={!enabled || state === "pending"}>
          {text(state === "pending" ? "import_pending" : uncertain ? "import_retry" : "import_submit")}
        </button>}
      {state !== "idle" && state !== "pending" ? <p role={saved ? "status" : "alert"} aria-live="polite">
        {text(state === "invalid" ? "import_invalid" : state === "saved_refresh_failed" ? "import_saved_refresh_failed"
          : state === "saved" ? "import_saved" : uncertain ? "import_uncertain" : "import_failed")}
      </p> : null}
    </form>
  </section>;
}
