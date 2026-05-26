"use client";

import type { WalkthroughStepId, WalkthroughVariant } from "../constants";
import { DashboardPanel } from "./dashboard-panel";
import { DescribePanel } from "./describe-panel";
import { RepairDashboardPanel } from "./repair-dashboard-panel";
import { RepairDescribePanel } from "./repair-describe-panel";
import { RepairLoadPanel } from "./repair-load-panel";
import { RepairReviewPanel } from "./repair-review-panel";
import { ReviewPanel } from "./review-panel";
import { UploadPanel } from "./upload-panel";

export interface MockupPanelProps {
  readonly stepId: WalkthroughStepId;
  readonly variant: WalkthroughVariant;
  readonly isActive: boolean;
  readonly onAdvance?: () => void;
}

export function MockupPanel({
  stepId,
  variant,
  isActive,
  onAdvance,
}: MockupPanelProps) {
  if (variant === "repair") {
    switch (stepId) {
      case "start":
        return (
          <RepairDashboardPanel isActive={isActive} onAdvance={onAdvance} />
        );
      case "upload":
        return <RepairLoadPanel isActive={isActive} onAdvance={onAdvance} />;
      case "describe":
        return (
          <RepairDescribePanel isActive={isActive} onAdvance={onAdvance} />
        );
      case "review":
        return (
          <RepairReviewPanel isActive={isActive} onAdvance={onAdvance} />
        );
    }
  }

  switch (stepId) {
    case "start":
      return <DashboardPanel isActive={isActive} onAdvance={onAdvance} />;
    case "upload":
      return <UploadPanel isActive={isActive} onAdvance={onAdvance} />;
    case "describe":
      return <DescribePanel isActive={isActive} onAdvance={onAdvance} />;
    case "review":
      return <ReviewPanel isActive={isActive} onAdvance={onAdvance} />;
  }
}
