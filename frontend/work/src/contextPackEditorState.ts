export type RetryIdentity = Readonly<{ signature: string; key: string }>;

export class ContextPackRetryIdentity {
  private current: RetryIdentity | null = null;

  constructor(private readonly newKey: () => string = () => crypto.randomUUID()) {}

  forOperation(signature: string): string {
    if (this.current?.signature !== signature) {
      this.current = { signature, key: this.newKey() };
    }
    return this.current.key;
  }

  reset(): void {
    this.current = null;
  }
}
