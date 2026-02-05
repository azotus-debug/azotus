import { motion } from "framer-motion";
import type { Issue } from "../types";
import { ActionButtons } from "./ActionButtons";

interface IssueCardProps {
  issue: Issue;
  onAccept: () => void;
  onEdit: () => void;
  onSkip: () => void;
}

export function IssueCard({ issue, onAccept, onEdit, onSkip }: IssueCardProps) {
  const badgeClasses =
    issue.severity === "error"
      ? "bg-review-error/10 text-review-error border-review-error/30"
      : "bg-review-warning/10 text-review-warning border-review-warning/30";

  const suggestionAvailable = issue.suggested_text && issue.suggested_text !== issue.current_text;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.25 }}
      className="bg-review-card/90 border border-review-border rounded-2xl p-6 space-y-6 shadow-[0_20px_60px_rgba(0,0,0,0.35)]"
    >
      <div className="flex items-center justify-between">
        <span className={`px-3 py-1 rounded-full text-xs font-semibold border ${badgeClasses}`}>
          {issue.severity === "error" ? "Critical" : "Review"}
        </span>
        <span className="text-xs text-gray-500">Segment {issue.segment_index + 1}</span>
      </div>

      <div className="space-y-4">
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-[0.2em]">Original</p>
          <p className="text-gray-400 mt-2 leading-relaxed">{issue.original_text}</p>
        </div>

        <div>
          <p className="text-xs text-gray-500 uppercase tracking-[0.2em]">Current Translation</p>
          <p className="text-white text-lg mt-2 leading-relaxed">{issue.current_text}</p>
        </div>
      </div>

      <div className={`flex items-start gap-3 p-4 rounded-xl border ${badgeClasses}`}>
        <div className="w-8 h-8 rounded-full border border-current flex items-center justify-center text-sm">
          {issue.severity === "error" ? "!" : "i"}
        </div>
        <div>
          <p className="text-sm font-semibold">{issue.type.replace(/_/g, " ")}</p>
          <p className="text-sm text-gray-200 mt-1">{issue.message}</p>
        </div>
      </div>

      {suggestionAvailable ? (
        <div>
          <p className="text-xs text-review-accent uppercase tracking-[0.2em]">Suggested Fix</p>
          <p className="text-white text-lg mt-2 bg-review-accent/10 p-4 rounded-xl border border-review-accent/30">
            {issue.suggested_text}
          </p>
        </div>
      ) : null}

      <ActionButtons
        onAccept={onAccept}
        onEdit={onEdit}
        onSkip={onSkip}
        acceptLabel={suggestionAvailable ? "Accept Fix" : "Accept As Is"}
      />
    </motion.div>
  );
}
