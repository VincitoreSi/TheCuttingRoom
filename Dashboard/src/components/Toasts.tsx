import { AnimatePresence, motion } from "framer-motion";
import { dismissToast, useToasts } from "../lib/toasts";
import { useReducedMotion } from "../lib/hooks";

/* The one place a failed request is allowed to speak.

   Deliberately not auto-dismissing on errors: the message is usually an instruction ("Add
   a handle in Config first"), and a sentence that vanishes after three seconds is barely
   better than the silence this replaces. The user closes it when they have read it. */
export function Toasts() {
  const toasts = useToasts();
  const reduced = useReducedMotion();

  return (
    <div className="toasts" role="region" aria-label="Notifications">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout={!reduced}
            initial={reduced ? false : { opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, x: 12 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className={`toast toast--${t.kind}`}
            role={t.kind === "error" ? "alert" : "status"}
          >
            <div className="toast__body">
              <div className="toast__title">{t.title}</div>
              {t.detail && <div className="toast__detail">{t.detail}</div>}
            </div>
            <button
              type="button"
              className="toast__close"
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss"
            >
              ×
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
