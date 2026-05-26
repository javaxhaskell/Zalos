"use client";

import { useEffect, useState } from "react";

/**
 * Returns true when the user prefers reduced motion (a11y).
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return reduced;
}

/** Stagger delay for sequenced card reveals (ms). */
export function staggerDelay(index: number, stepMs = 45): number {
  return Math.min(index * stepMs, 240);
}
