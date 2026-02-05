interface ActionButtonsProps {
  onAccept: () => void;
  onEdit: () => void;
  onSkip: () => void;
  acceptLabel?: string;
}

export function ActionButtons({ onAccept, onEdit, onSkip, acceptLabel = "Accept Fix" }: ActionButtonsProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row">
        <button
          onClick={onAccept}
          className="flex-1 bg-review-accent text-black font-semibold py-3 px-6 rounded-lg hover:bg-review-accent/90 transition"
        >
          {acceptLabel}
        </button>
        <button
          onClick={onEdit}
          className="flex-1 bg-review-card border border-review-border text-white font-semibold py-3 px-6 rounded-lg hover:bg-review-border transition"
        >
          Edit
        </button>
        <button
          onClick={onSkip}
          className="px-6 py-3 text-gray-400 hover:text-white transition"
        >
          Skip
        </button>
      </div>
      <div className="flex flex-wrap justify-center gap-4 text-xs text-gray-500">
        <span>
          <kbd className="px-2 py-1 bg-review-border rounded">Enter</kbd> Accept
        </span>
        <span>
          <kbd className="px-2 py-1 bg-review-border rounded">E</kbd> Edit
        </span>
        <span>
          <kbd className="px-2 py-1 bg-review-border rounded">Right</kbd> Skip
        </span>
      </div>
    </div>
  );
}
