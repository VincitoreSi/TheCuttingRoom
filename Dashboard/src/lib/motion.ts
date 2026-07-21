// Shared entrance-motion recipe (S1 / R4 of the redesign consistency directive).
// One easing curve, one stagger shape — every section across Playbook,
// PipelineBoard, and Activity imports this instead of inlining its own.

export const EASE = [0.22, 1, 0.36, 1] as const;

export function sectionMotion(i: number, reduced: boolean) {
  return {
    initial: reduced ? false : ({ opacity: 0, y: 8 } as const),
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.3, ease: EASE, delay: Math.min(i * 0.06, 0.24) },
  };
}
