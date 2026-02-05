import { motion } from "framer-motion";
import type { ReviewData } from "../types";

interface CompletionScreenProps {
  data: ReviewData;
  reviewedCount: number;
  onApprove: () => void;
}

export function CompletionScreen({ data, reviewedCount, onApprove }: CompletionScreenProps) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.35 }}
      className="min-h-screen flex items-center justify-center p-6"
    >
      <div className="w-full max-w-xl bg-review-card/90 border border-review-border rounded-3xl p-8 text-center space-y-6">
        <div className="mx-auto w-20 h-20 rounded-full border border-review-accent text-review-accent flex items-center justify-center text-2xl font-semibold">
          OK
        </div>
        <div>
          <h1 className="text-3xl font-display font-semibold">Review complete</h1>
          <p className="text-gray-400 mt-2">You handled {reviewedCount} issues</p>
        </div>
        <div className="space-y-1">
          <p className="text-5xl font-display font-semibold text-review-accent">{data.total_segments}</p>
          <p className="text-gray-400">segments approved for delivery</p>
        </div>
        <button
          onClick={onApprove}
          className="w-full bg-review-accent text-black font-semibold text-lg py-4 rounded-xl hover:bg-review-accent/90 transition"
        >
          Deliver to client
        </button>
        <p className="text-xs text-gray-500">You can close this window once delivery starts.</p>
      </div>
    </motion.div>
  );
}
