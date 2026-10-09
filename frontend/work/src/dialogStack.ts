/**
 * Which modal surfaces are open, innermost last.
 *
 * A native `<dialog>` already traps focus and answers Escape itself, and the
 * `cancel` event only reaches the topmost one. What it cannot do is stop the
 * surfaces *underneath* from reacting: the shell listens for Escape on the
 * window, and a task panel used to close itself whenever Escape was pressed
 * anywhere. With a conversation or a result gallery opened from inside a task
 * detail, that closed the wrong thing — sometimes two things at once.
 *
 * So every modal registers here while it is open. An underlying surface asks
 * `isInnermostDialog` (or `anyDialogOpen` for the window handler) before it
 * acts on a key, and a nested dialog closes alone.
 *
 * This is presentation state in RAM: no storage, no route, no server.
 */
let counter = 0;
const stack: string[] = [];
const listeners = new Set<() => void>();

function publish(): void {
  for (const listener of [...listeners]) listener();
}

/** Register an open modal. The returned function must run when it closes. */
export function pushDialog(): { id: string; release: () => void } {
  const id = `dialog-${++counter}`;
  stack.push(id);
  publish();
  let released = false;
  return {
    id,
    release: () => {
      if (released) return;
      released = true;
      const at = stack.lastIndexOf(id);
      if (at !== -1) stack.splice(at, 1);
      publish();
    }
  };
}

/** True while any modal is open, including one opened from inside another. */
export function anyDialogOpen(): boolean {
  return stack.length > 0;
}

export function dialogDepth(): number {
  return stack.length;
}

/** True only for the modal the person is actually looking at. */
export function isInnermostDialog(id: string): boolean {
  return stack.length > 0 && stack[stack.length - 1] === id;
}

export function subscribeDialogs(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Tests only: start from a known empty stack. */
export function resetDialogStack(): void {
  stack.length = 0;
  publish();
}
