import type { LibraryApi } from "./libraryApi.js";
import type { LibraryDetail, LibraryFile, LibraryItem } from "./libraryTypes.js";

export type LibraryDetailView = Readonly<{
  item: LibraryItem | null;
  status: "closed" | "loading" | "ready" | "error";
  detail: LibraryDetail | null;
  file: Readonly<{ path: string; text: string | null; error: boolean }> | null;
}>;
const closed: LibraryDetailView = { item: null, status: "closed", detail: null, file: null };

/** A read-only detail lifetime. Closing invalidates even transports ignoring abort. */
export class LibraryDetailSession {
  private state: LibraryDetailView = closed;
  private listeners = new Set<() => void>();
  private reading: AbortController | null = null;
  private fileReading: AbortController | null = null;
  constructor(private api: Pick<LibraryApi, "detail" | "readFile">) {}
  snapshot = (): LibraryDetailView => this.state;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private publish(state: LibraryDetailView): void { this.state = state; this.listeners.forEach((listener) => listener()); }
  close = (): void => {
    this.reading?.abort(); this.fileReading?.abort();
    this.reading = null; this.fileReading = null; this.publish(closed);
  };
  open = async (item: LibraryItem): Promise<void> => {
    this.reading?.abort(); this.fileReading?.abort(); this.fileReading = null;
    const request = new AbortController(); this.reading = request;
    this.publish({ item, status: "loading", detail: null, file: null });
    try {
      const detail = await this.api.detail(item.ref, request.signal);
      if (this.reading === request && !request.signal.aborted) this.publish({ item, status: "ready", detail, file: null });
    } catch {
      if (this.reading === request && !request.signal.aborted) this.publish({ item, status: "error", detail: null, file: null });
    }
  };
  retry = async (): Promise<void> => { if (this.state.item) await this.open(this.state.item); };
  readFile = async (file: LibraryFile): Promise<void> => {
    const detail = this.state.detail;
    if (!detail) return;
    this.fileReading?.abort();
    const request = new AbortController(); this.fileReading = request;
    this.publish({ ...this.state, file: { path: file.path, text: null, error: false } });
    try {
      const text = await this.api.readFile(detail.ref, file, request.signal);
      if (this.fileReading === request && !request.signal.aborted) this.publish({ ...this.state, file: { path: file.path, text, error: false } });
    } catch {
      if (this.fileReading === request && !request.signal.aborted) this.publish({ ...this.state, file: { path: file.path, text: null, error: true } });
    }
  };
}
