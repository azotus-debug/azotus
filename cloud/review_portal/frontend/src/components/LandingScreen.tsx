import { motion } from "framer-motion";
import type { ReviewData } from "../types";

interface LandingScreenProps {
  data: ReviewData;
  onStart: () => void;
  onApproveAll: () => void;
  onWatchOnly?: () => void;
}

export function LandingScreen({ data, onStart, onApproveAll, onWatchOnly }: LandingScreenProps) {
  const hasIssues = data.needs_review > 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="min-h-screen flex items-center justify-center p-6"
    >
      <div className="w-full max-w-2xl space-y-8">
        <div className="text-center space-y-3">
          <p className="text-xs uppercase tracking-[0.4em] text-gray-500">Review Portal</p>
          <h1 className="text-4xl md:text-5xl font-display font-semibold">{data.program_name}</h1>
          <p className="text-lg text-gray-400">{data.language} Translation Review</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-review-card/90 border border-review-border rounded-2xl p-6">
            <p className="text-sm text-gray-500 uppercase tracking-[0.2em]">Ready</p>
            <p className="text-5xl font-display font-semibold text-review-accent mt-3">{data.auto_approved}</p>
            <p className="text-sm text-gray-400 mt-2">segments already clean</p>
          </div>
          <div className="bg-review-card/90 border border-review-border rounded-2xl p-6">
            <p className="text-sm text-gray-500 uppercase tracking-[0.2em]">Needs review</p>
            <p className="text-5xl font-display font-semibold text-review-warning mt-3">{data.needs_review}</p>
            <p className="text-sm text-gray-400 mt-2">issues waiting on you</p>
          </div>
        </div>

        <div className="bg-review-card/90 border border-review-border rounded-2xl p-6 flex flex-col items-center gap-4 text-center">
          <p className="text-sm text-gray-500 uppercase tracking-[0.2em]">Estimated time</p>
          <p className="text-3xl font-display font-semibold">{data.estimated_time}</p>
          {hasIssues ? (
            <button
              onClick={onStart}
              className="w-full bg-review-accent text-black font-semibold text-lg py-4 rounded-xl hover:bg-review-accent/90 transition"
            >
              Begin Review
            </button>
          ) : (
            <button
              onClick={onApproveAll}
              className="w-full bg-review-accent text-black font-semibold text-lg py-4 rounded-xl hover:bg-review-accent/90 transition"
            >
              Approve and Deliver
            </button>
          )}
          <p className="text-xs text-gray-500">Stay focused. One issue at a time.</p>
        </div>

        {/* Watch Only option for stakeholders */}
        {onWatchOnly && (
          <div className="text-center">
            <button
              onClick={onWatchOnly}
              className="text-gray-400 hover:text-white transition underline underline-offset-4 text-sm"
            >
              Just watch the video with subtitles →
            </button>
            <p className="text-xs text-gray-600 mt-2">For stakeholders who want to preview without editing</p>
          </div>
        )}
      </div>
    </motion.div>
  );
}
