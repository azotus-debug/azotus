import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useReviewStore } from "./store/reviewStore";
import { LandingScreen } from "./components/LandingScreen";
import { CompletionScreen } from "./components/CompletionScreen";
import { WatchOnlyScreen } from "./components/WatchOnlyScreen";
import { VideoPlayer } from "./components/VideoPlayer";
import { IssueCard } from "./components/IssueCard";
import { ProgressIndicator } from "./components/ProgressIndicator";
import { EditModal } from "./components/EditModal";
import { useReviewData } from "./hooks/useReviewData";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import type { ReviewData } from "./types";

type Screen = "landing" | "review" | "complete" | "watch";

const mockData: ReviewData = {
  job_id: "test-job",
  program_name: "Jerusalem Dateline",
  language: "Dutch",
  total_segments: 329,
  auto_approved: 326,
  needs_review: 3,
  estimated_time: "~3 minutes",
  video_url: "",
  issues: [
    {
      id: "issue_47",
      segment_index: 47,
      type: "reading_speed",
      message: "This subtitle may be too fast to read",
      severity: "warning",
      original_text: "The people of Iran are rising up against the regime",
      current_text: "Het volk van Iran komt in opstand tegen het regime en eist vrijheid",
      suggested_text: "Het Iraanse volk komt in opstand",
      timestamp_start: 124.5,
      timestamp_end: 127.8,
    },
    {
      id: "issue_112",
      segment_index: 112,
      type: "terminology",
      message: "The translation of a key term looks inconsistent",
      severity: "error",
      original_text: "Grace is at the heart of the Gospel message",
      current_text: "Gratie staat centraal in de boodschap van het Evangelie",
      suggested_text: "Genade staat centraal in de boodschap van het Evangelie",
      timestamp_start: 355.1,
      timestamp_end: 359.0,
    },
    {
      id: "issue_205",
      segment_index: 205,
      type: "line_length",
      message: "This line is likely too long for broadcast",
      severity: "warning",
      original_text: "They are calling for a peaceful transition of power",
      current_text: "Zij roepen op tot een vreedzame overdracht van de macht in het land",
      suggested_text: "Zij roepen op tot een vreedzame machtswisseling",
      timestamp_start: 622.2,
      timestamp_end: 626.4,
    },
  ],
};

function App() {
  const [screen, setScreen] = useState<Screen>("landing");
  const [isEditOpen, setEditOpen] = useState(false);
  const [draftText, setDraftText] = useState("");

  const {
    data,
    isLoading,
    error,
    getCurrentIssue,
    resolveIssue,
    currentIssueIndex,
    resolvedIssues,
    isComplete,
  } = useReviewStore();

  // Check if we have a job_id in the URL - if so, use live data
  const hasJobInUrl = window.location.pathname.split("/").filter(Boolean).length > 0
    && window.location.search.includes("token=");

  useReviewData(hasJobInUrl ? {} : { mockData });

  const currentIssue = getCurrentIssue();

  const handleAccept = () => {
    if (!currentIssue) return;
    const nextText = currentIssue.suggested_text || currentIssue.current_text;
    resolveIssue("accept", nextText);
  };

  const handleEdit = () => {
    if (!currentIssue) return;
    setDraftText(currentIssue.current_text);
    setEditOpen(true);
  };

  const handleSkip = () => {
    resolveIssue("skip");
  };

  const handleSaveEdit = () => {
    if (!currentIssue) return;
    const nextText = draftText.trim() || currentIssue.current_text;
    resolveIssue("edit", nextText);
    setEditOpen(false);
  };

  useKeyboardShortcuts({
    enabled: screen === "review" && !!currentIssue && !isEditOpen,
    onAccept: handleAccept,
    onEdit: handleEdit,
    onSkip: handleSkip,
  });

  const headerTitle = useMemo(() => {
    if (!data) return "";
    return `${data.program_name} - ${data.language}`;
  }, [data]);

  if (isLoading && !data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-400">Loading review data...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-400">{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-400">Preparing review...</div>
      </div>
    );
  }

  if (screen === "landing") {
    return (
      <LandingScreen
        data={data}
        onStart={() => setScreen("review")}
        onApproveAll={() => setScreen("complete")}
        onWatchOnly={() => setScreen("watch")}
      />
    );
  }

  if (screen === "watch") {
    return (
      <WatchOnlyScreen
        data={data}
        onBack={() => setScreen("landing")}
      />
    );
  }

  if (screen === "complete" || (screen === "review" && isComplete())) {
    return (
      <CompletionScreen
        data={data}
        reviewedCount={resolvedIssues.size}
        onApprove={() => alert("Delivered!")}
      />
    );
  }

  if (!currentIssue) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-400">No issues to review.</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.4em] text-gray-500">Focus mode</p>
            <h1 className="text-2xl font-display font-semibold">{headerTitle}</h1>
          </div>
          <ProgressIndicator current={currentIssueIndex} total={data.issues.length} />
        </header>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="grid gap-6 lg:grid-cols-[1.4fr_1fr]"
        >
          <VideoPlayer
            videoUrl={data.video_url}
            currentSubtitle={currentIssue.current_text}
            timestamp={currentIssue.timestamp_start}
          />

          <AnimatePresence mode="wait">
            <IssueCard
              key={currentIssue.id}
              issue={currentIssue}
              onAccept={handleAccept}
              onEdit={handleEdit}
              onSkip={handleSkip}
            />
          </AnimatePresence>
        </motion.div>
      </div>

      <EditModal
        isOpen={isEditOpen}
        text={draftText}
        onChange={setDraftText}
        onSave={handleSaveEdit}
        onClose={() => setEditOpen(false)}
      />
    </div>
  );
}

export default App;
