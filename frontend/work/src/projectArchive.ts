export type ArchiveRequest = Readonly<{
  projectId: string;
  revision: string;
  name: string;
  /** The host exposes a checkout path only for the open project. */
  path: string | null;
}>;

export type ArchiveConfirmationState = Readonly<{
  open: boolean;
  request: ArchiveRequest | null;
  busy: boolean;
}>;

const CLOSED: ArchiveConfirmationState = { open: false, request: null, busy: false };

/**
 * Owns only browser lifetime: the pending archive question and its single
 * in-flight confirmation. It never archives by itself and never persists.
 */
export class ProjectArchiveConfirmation {
  private state: ArchiveConfirmationState = CLOSED;
  private readonly listeners = new Set<() => void>();

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  readonly snapshot = (): ArchiveConfirmationState => this.state;

  private publish(next: ArchiveConfirmationState): void {
    this.state = next;
    for (const listener of [...this.listeners]) listener();
  }

  /** Asking is the only effect of the menu action; nothing is archived yet. */
  ask(request: ArchiveRequest): void {
    if (this.state.busy) return;
    this.publish({ open: true, request, busy: false });
  }

  cancel(): void {
    if (this.state.busy) return;
    this.publish(CLOSED);
  }

  /**
   * Confirm issues exactly one archive request. A repeated click while the
   * first one is in flight is ignored rather than retried.
   */
  async confirm(archive: (request: ArchiveRequest) => Promise<boolean>): Promise<boolean> {
    const request = this.state.request;
    if (!this.state.open || request === null || this.state.busy) return false;
    this.publish({ open: true, request, busy: true });
    let archived = false;
    try {
      archived = await archive(request);
      return archived;
    } finally {
      if (this.state.request === request) {
        this.publish(archived ? CLOSED : { open: true, request, busy: false });
      }
    }
  }
}
