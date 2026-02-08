"use client";

import { useEffect, useState } from "react";
import Badge from "@/components/common/Badge";
import ProgressBar from "@/components/common/ProgressBar";
import PageHeader from "@/components/layout/PageHeader";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore, Track, PipelineStats } from "@/store/programs";
import { useSSEContext } from "@/components/SSEProvider";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

// Stage order for grouping
const STAGE_ORDER = [
  "QUEUED",
  "INGESTING",
  "TRANSCRIBING",
  "TRANSLATING",
  "TRANSLATING_CLOUD_SUBMITTED",
  "CLOUD_TRANSLATING",
  "CLOUD_REVIEWING",
  "CLOUD_EDITING",
  "AWAITING_REVIEW",
  "AWAITING_APPROVAL",
  "FAILED",
  "FINALIZING",
  "BURNING",
  "DUBBING",
];

// Stage to display name
const STAGE_LABELS: Record<string, string> = {
  QUEUED: "⏳ Queued",
  INGESTING: "📥 Ingesting",
  TRANSCRIBING: "🎤 Transcribing",
  TRANSLATING: "🌍 Translating",
  TRANSLATING_CLOUD_SUBMITTED: "☁️ Cloud Submitted",
  CLOUD_TRANSLATING: "☁️ Cloud Translating",
  CLOUD_REVIEWING: "🔍 Cloud Review",
  CLOUD_EDITING: "✏️ Cloud Editing",
  AWAITING_REVIEW: "👀 Awaiting Review",
  AWAITING_APPROVAL: "📝 Awaiting Approval",
  FAILED: "🧨 Failed",
  FINALIZING: "📦 Finalizing",
  BURNING: "🔥 Burning",
  DUBBING: "🎙️ Dubbing",
};

const BLOCKED_STAGES = new Set(["AWAITING_REVIEW", "AWAITING_APPROVAL", "FAILED"]);

const getStageCount = (stats: PipelineStats | null, stage: string): number | null => {
  if (!stats?.stages) return null;
  if (Array.isArray(stats.stages)) {
    const entry = stats.stages.find((item) => item.stage === stage);
    return entry ? entry.count : null;
  }
  if (typeof stats.stages === "object") {
    const value = stats.stages[stage];
    return typeof value === "number" ? value : null;
  }
  return null;
};

// Track with program info
interface TrackWithProgram extends Track {
  program_title?: string;
}

export default function PipelineView() {
  const { selectProgram } = useNavigation();
  const { activeTracks, programs, pipelineStats, fetchActiveTracks, fetchPrograms, fetchPipelineStats } =
    useProgramsStore();
  const [retrying, setRetrying] = useState<Record<string, boolean>>({});
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});

  const { subscribe } = useSSEContext();

  useEffect(() => {
    fetchActiveTracks();
    fetchPrograms();
    fetchPipelineStats();
  }, [fetchActiveTracks, fetchPrograms, fetchPipelineStats]);

  // SSE subscriptions for real-time updates
  useEffect(() => {
    const unsubs = [
      subscribe("pipeline_updated", () => {
        fetchActiveTracks();
        fetchPipelineStats();
      }),
      subscribe("tracks_updated", () => {
        fetchActiveTracks();
        fetchPrograms();
        fetchPipelineStats();
      }),
      subscribe("track_progress", () => {
        fetchActiveTracks();
      }),
    ];
    return () => unsubs.forEach(fn => fn());
  }, [subscribe, fetchActiveTracks, fetchPrograms, fetchPipelineStats]);

  // Group tracks by stage
  const tracksByStage: Record<string, TrackWithProgram[]> = {};

  activeTracks.forEach((track) => {
    const stage = track.stage || "QUEUED";
    if (!tracksByStage[stage]) {
      tracksByStage[stage] = [];
    }
    // Find program for this track
    const program = programs.find((p) => p.id === track.program_id);
    tracksByStage[stage].push({
      ...track,
      program_title: program?.title || track.program_id?.slice(0, 8),
    });
  });

  // Order stages
  const orderedStages = STAGE_ORDER.filter((s) => tracksByStage[s]?.length > 0);
  // Add any other stages not in our order
  Object.keys(tracksByStage).forEach((stage) => {
    if (!orderedStages.includes(stage)) {
      orderedStages.push(stage);
    }
  });

  const totalActive = pipelineStats?.total_active ?? activeTracks.length;
  const failedCount = getStageCount(pipelineStats, "FAILED") ?? activeTracks.filter((t) => t.stage === "FAILED").length;
  const needsReviewCount =
    getStageCount(pipelineStats, "AWAITING_REVIEW") ??
    activeTracks.filter((t) => t.stage === "AWAITING_REVIEW").length;
  const blockedCount =
    pipelineStats?.blocked ??
    activeTracks.filter((t) => BLOCKED_STAGES.has(t.stage || "QUEUED")).length;

  const handleRetry = async (trackId: string) => {
    setRetrying((prev) => ({ ...prev, [trackId]: true }));
    setRetryErrors((prev) => {
      const next = { ...prev };
      delete next[trackId];
      return next;
    });

    try {
      const res = await fetch(`${API_BASE}/api/v2/tracks/${trackId}/retry`, { method: "POST" });
      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        throw new Error(payload?.error || "Retry failed");
      }
      await fetchActiveTracks();
      await fetchPrograms();
      await fetchPipelineStats();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Retry failed";
      setRetryErrors((prev) => ({ ...prev, [trackId]: message.slice(0, 64) }));
    } finally {
      setRetrying((prev) => ({ ...prev, [trackId]: false }));
    }
  };

  return (
    <section className="pipeline-view">
      <PageHeader
        title="Pipeline"
        subtitle={`${totalActive} track${totalActive !== 1 ? "s" : ""} in progress`}
      />

      <div className="pipeline-summary">
        <div className="summary-item">
          <span className="summary-label">Active</span>
          <span className="summary-value">{totalActive}</span>
        </div>
        <span className="summary-divider" />
        <div className="summary-item">
          <span className="summary-label">Blocked</span>
          <span className="summary-value summary-value--alert">{blockedCount}</span>
        </div>
        <span className="summary-divider" />
        <div className="summary-item">
          <span className="summary-label">Needs Review</span>
          <span className="summary-value">{needsReviewCount}</span>
        </div>
        <span className="summary-divider" />
        <div className="summary-item">
          <span className="summary-label">Failed</span>
          <span className="summary-value summary-value--alert">{failedCount}</span>
        </div>
      </div>

      {totalActive === 0 ? (
        <div className="empty-state">
          <span style={{ fontSize: "48px" }}>✨</span>
          <p>No active work - all caught up!</p>
        </div>
      ) : (
        <div className="stage-groups">
          {orderedStages.map((stage) => (
            <div key={stage} className="stage-group">
              <div className="stage-header stage-header--sticky">
                <span className="stage-title">{STAGE_LABELS[stage] || stage}</span>
                <Badge
                  label={String(getStageCount(pipelineStats, stage) ?? tracksByStage[stage].length)}
                  variant="info"
                />
              </div>
              <div className="stage-tracks">
                {tracksByStage[stage].map((track) => (
                  (() => {
                    const stageKey = track.stage || "QUEUED";
                    const progressValue = Number.isFinite(track.progress) ? track.progress : 0;
                    const isBlocked = BLOCKED_STAGES.has(stageKey);
                    const isIdle = stageKey === "QUEUED" || progressValue === 0;
                    const indicator = isBlocked ? "blocked" : isIdle ? "idle" : "active";
                    const badgeVariant = stageKey === "FAILED" ? "error" : isBlocked ? "warning" : "info";
                    const rowClass = `pipeline-track-row${isIdle ? " track-row--idle" : ""}`;

                    return (
                      <div
                        key={track.id}
                        className={rowClass}
                        role="button"
                        tabIndex={0}
                        onClick={() => selectProgram(track.program_id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") selectProgram(track.program_id);
                        }}
                      >
                        <div className="track-info">
                          <span className={`status-indicator indicator--${indicator}`} />
                          <div className="track-text">
                            <span className="track-program">{track.program_title}</span>
                            <span className="track-language">
                              {track.language_name} {track.type === "dub" ? "Dub" : "Sub"}
                            </span>
                          </div>
                        </div>
                        <div className="track-progress">
                          <ProgressBar value={progressValue} label={`${track.language_name} progress`} />
                          <span className="progress-text">{Math.round(progressValue)}%</span>
                        </div>
                        <div className="track-status-badge">
                          <Badge
                            label={track.status?.slice(0, 20) || "Working"}
                            variant={badgeVariant}
                          />
                          {stageKey === "FAILED" && (
                            <>
                              <button
                                type="button"
                                className="retry-button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  handleRetry(track.id);
                                }}
                                disabled={!!retrying[track.id]}
                                title={retryErrors[track.id] || "Retry failed track"}
                              >
                                {retrying[track.id] ? "Retrying..." : "Retry"}
                              </button>
                              {retryErrors[track.id] && (
                                <span className="retry-error">{retryErrors[track.id]}</span>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    );
                  })()
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
