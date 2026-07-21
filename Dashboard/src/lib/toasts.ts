import { useSyncExternalStore } from "react";

/* Somewhere for a failed request to show up.

   Every mutation in the app used to swallow its error: no `onError` anywhere, no error
   region in any view. So a refused run — the hub answering 409 with a written explanation,
   or 400 because the route did not resolve — reached the browser, was discarded by
   react-query, and the click looked like it had simply done nothing. That is the single
   most common report about this Dashboard.

   A module-level store rather than context: mutations are declared in lib/hooks.ts, and
   threading a provider through every one of them to show a message is more machinery than
   the job needs. Same shape as the log stream. */

export type ToastKind = "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  /** the hub's own sentence, shown verbatim — it is written for the person who clicked */
  detail?: string;
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function pushToast(t: Omit<Toast, "id">): number {
  const id = nextId++;
  // Identical back-to-back messages collapse: a double-click on a blocked Run should not
  // stack two copies of the same sentence.
  const last = toasts[toasts.length - 1];
  if (last && last.title === t.title && last.detail === t.detail) return last.id;
  toasts = [...toasts, { ...t, id }];
  emit();
  return id;
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

/** Test seam — the store is module-level, so it outlives a component tree. */
export function resetToasts() {
  toasts = [];
  nextId = 1;
  emit();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

const snapshot = () => toasts;

export function useToasts(): Toast[] {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** What to say when a mutation rejects. Keeps the hub's `detail` as the body. */
export function toastForError(action: string, err: unknown) {
  const detail =
    err && typeof err === "object" && "detail" in err
      ? String((err as { detail: unknown }).detail)
      : err instanceof Error
        ? err.message
        : String(err);
  pushToast({ kind: "error", title: action, detail });
}
