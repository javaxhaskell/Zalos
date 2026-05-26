import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export interface NextStepItem {
  readonly text: string;
}

export interface NextStepsCardProps {
  readonly title?: string;
  readonly items: readonly NextStepItem[];
}

/**
 * Compact post-completion checklist for finance users on Author/Repair
 * wizard terminal screens.
 */
export function NextStepsCard({
  title = "Next steps",
  items,
}: NextStepsCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed text-slate-700">
          {items.map((item, index) => (
            <li key={index}>{item.text}</li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
