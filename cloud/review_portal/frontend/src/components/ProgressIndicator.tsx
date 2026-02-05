interface ProgressIndicatorProps {
  current: number;
  total: number;
}

export function ProgressIndicator({ current, total }: ProgressIndicatorProps) {
  const clampedTotal = Math.max(total, 1);
  const progress = Math.min((current + 1) / clampedTotal, 1);

  return (
    <div className="flex items-center gap-4 min-w-[200px]">
      <div className="flex-1 h-2 bg-review-border rounded-full overflow-hidden">
        <div
          className="h-full bg-review-accent transition-all"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      <span className="text-xs text-gray-400">
        {Math.min(current + 1, total)} / {total}
      </span>
    </div>
  );
}
