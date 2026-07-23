import { useEffect } from "react";
import { motion } from "framer-motion";
import { AgentConfigForm } from "../agent/AgentConfigForm";
import { IconX } from "../icons";
import { EASE } from "../../lib/motion";
import { humanizeAgent } from "../../lib/agents";
import { useReducedMotion } from "../../lib/hooks";

/* -------- the one place AgentConfigForm is rendered --------
   The per-agent config editor used to live inline on two board surfaces (The
   Floor and the agent desk). It now has a single home: this modal, opened from
   the Keys & models panel in the Config section. Boards keep a Config button
   that navigates here (see App's openAgentConfig). Follows the ReelModal shell
   — a .modal-scrim dialog with Escape-to-close, backdrop-click close, and the
   shared .modal / .modal__close classes. */
export function AgentConfigModal({ agent, onClose }: { agent: string; onClose: () => void }) {
  const reduced = useReducedMotion();

  // dismiss on Escape and lock background scroll, matching the ReelModal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div
      className="modal-scrim"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Configuration: ${humanizeAgent(agent)}`}
    >
      {/* single-column form modal — same width override the proposal modal uses */}
      <motion.div
        className="modal"
        style={{ gridTemplateColumns: "1fr", width: "min(680px, 100%)", position: "relative" }}
        onClick={(e) => e.stopPropagation()}
        initial={reduced ? false : { opacity: 0, scale: 0.97, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.28, ease: EASE }}
      >
        <button className="modal__close" onClick={onClose} aria-label="Close">
          <IconX size={16} />
        </button>
        <div className="modal__body">
          <div className="min-w-0">
            <div className="font-display text-[22px] leading-tight">{humanizeAgent(agent)}</div>
            <div className="eyebrow mt-1">Configuration</div>
          </div>
          <AgentConfigForm agent={agent} />
        </div>
      </motion.div>
    </div>
  );
}
