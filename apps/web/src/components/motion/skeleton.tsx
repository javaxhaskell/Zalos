import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface SkeletonLineProps {
  readonly className?: string;
}

export function SkeletonLine({ className }: SkeletonLineProps) {
  return (
    <div
      className={cn("h-4 animate-pulse rounded bg-slate-200/90", className)}
      aria-hidden
    />
  );
}

export interface SkeletonCardProps {
  readonly lines?: number;
  readonly className?: string;
}

export function SkeletonCard({ lines = 3, className }: SkeletonCardProps) {
  return (
    <Card className={className} aria-busy="true" aria-label="Loading">
      <CardHeader className="space-y-2 pb-2">
        <SkeletonLine className="h-5 w-40" />
        <SkeletonLine className="h-3 w-full max-w-md" />
      </CardHeader>
      <CardContent className="space-y-2">
        {Array.from({ length: lines }, (_, i) => (
          <SkeletonLine
            key={i}
            className={i === lines - 1 ? "w-2/3" : "w-full"}
          />
        ))}
      </CardContent>
    </Card>
  );
}

export function SkeletonTableRows({
  rows = 4,
  cols = 4,
}: {
  readonly rows?: number;
  readonly cols?: number;
}) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading table">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }, (_, c) => (
            <SkeletonLine
              key={c}
              className={cn("h-3 flex-1", c === cols - 1 && "max-w-[4rem]")}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
