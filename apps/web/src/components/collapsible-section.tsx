"use client";

import { SmoothCollapse, type SmoothCollapseProps } from "@/components/motion/smooth-collapse";

export type CollapsibleSectionProps = SmoothCollapseProps;

/**
 * Progressive-disclosure wrapper — delegates to SmoothCollapse.
 */
export function CollapsibleSection(props: CollapsibleSectionProps) {
  return <SmoothCollapse {...props} />;
}
