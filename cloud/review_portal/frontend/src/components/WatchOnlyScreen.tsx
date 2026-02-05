import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import type { ReviewData } from "../types";

interface WatchOnlyScreenProps {
  data: ReviewData;
  onBack: () => void;
}

export function WatchOnlyScreen({ data, onBack }: WatchOnlyScreenProps) {
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  // Get all segments sorted by start time
  const allSegments = data.issues.map((issue) => ({
    text: issue.current_text,
    start: issue.timestamp_start,
    end: issue.timestamp_end,
  }));

  // Find current subtitle based on video time
  const currentSubtitle = allSegments.find(
    (seg) => currentTime >= seg.start && currentTime <= seg.end
  );

  // Handle video time updates
  const handleTimeUpdate = () => {
    if (videoRef.current) {
      setCurrentTime(videoRef.current.currentTime);
    }
  };

  const handlePlayPause = () => {
    if (videoRef.current) {
      if (isPlaying) {
        videoRef.current.pause();
      } else {
        videoRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === " " || e.key === "k") {
        e.preventDefault();
        handlePlayPause();
      } else if (e.key === "Escape") {
        onBack();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isPlaying, onBack]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="min-h-screen bg-black flex flex-col"
    >
      {/* Header */}
      <div className="flex items-center justify-between p-4 bg-black/50">
        <button
          onClick={onBack}
          className="text-gray-400 hover:text-white transition flex items-center gap-2"
        >
          <span>←</span>
          <span>Back to Review</span>
        </button>
        <div className="text-center">
          <p className="text-xs text-gray-500 uppercase tracking-wider">Watch Mode</p>
          <h1 className="text-lg font-semibold">{data.program_name}</h1>
        </div>
        <div className="w-24" /> {/* Spacer for centering */}
      </div>

      {/* Video Player */}
      <div className="flex-1 relative flex items-center justify-center">
        {data.video_url ? (
          <div className="relative w-full max-w-5xl aspect-video">
            <video
              ref={videoRef}
              src={data.video_url}
              className="w-full h-full bg-black"
              onTimeUpdate={handleTimeUpdate}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              controls
            />

            {/* Subtitle overlay */}
            {currentSubtitle && (
              <div className="absolute bottom-16 left-0 right-0 text-center px-4 pointer-events-none">
                <div className="inline-block bg-black/80 px-6 py-3 rounded-lg">
                  <p className="text-2xl text-white font-medium leading-relaxed whitespace-pre-wrap">
                    {currentSubtitle.text}
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center space-y-6 max-w-lg">
            <div className="w-24 h-24 mx-auto rounded-full bg-gray-800 flex items-center justify-center">
              <svg className="w-12 h-12 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h2 className="text-2xl font-semibold text-white mb-2">Video Not Yet Available</h2>
              <p className="text-gray-400 mb-4">
                The video for "{data.program_name}" hasn't been uploaded yet.
                Once it's ready, you'll be able to watch with subtitles overlaid.
              </p>
              <p className="text-sm text-gray-500">
                {data.total_segments} subtitles ready • {data.language} translation
              </p>
            </div>
            <button
              onClick={onBack}
              className="bg-review-accent text-black font-semibold px-8 py-3 rounded-xl hover:bg-review-accent/90 transition"
            >
              Return to Review
            </button>
          </div>
        )}
      </div>

      {/* Footer with keyboard hints */}
      <div className="p-4 bg-black/50 text-center">
        <p className="text-xs text-gray-500">
          Press <kbd className="px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">Space</kbd> to play/pause •
          <kbd className="px-1.5 py-0.5 bg-gray-800 rounded text-gray-400 ml-2">Esc</kbd> to go back
        </p>
      </div>
    </motion.div>
  );
}
