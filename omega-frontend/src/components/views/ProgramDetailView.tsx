"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Badge from "@/components/common/Badge";
import Button from "@/components/common/Button";
import AddTrackModal from "@/components/common/AddTrackModal";
import Modal from "@/components/common/Modal";
import ProgressBar from "@/components/common/ProgressBar";
import TrackDetailPanel from "@/components/common/TrackDetailPanel";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore, Track } from "@/store/programs";
import { useSSEContext } from "@/components/SSEProvider";

interface Props {
  programId: string;
}

interface MasterSummary {
  id: string;
  state?: string;
  version?: number;
  locked_at?: string | null;
  locked_by?: string | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const WAVEFORM_BARS = [
  18, 32, 26, 40, 22, 36, 28, 48, 22, 30, 44, 20, 52, 28, 40, 24, 36, 30, 46, 22, 34, 26,
  42, 20, 36, 28, 48, 24, 38, 22, 44, 28, 40, 20,
];

const FLAG_MAP: Record<string, string> = {
  is: "🇮🇸",
  en: "🇬🇧",
  es: "🇪🇸",
  de: "🇩🇪",
  fr: "🇫🇷",
  pt: "🇵🇹",
  it: "🇮🇹",
  nl: "🇳🇱",
  sv: "🇸🇪",
  no: "🇳🇴",
  da: "🇩🇰",
  fi: "🇫🇮",
  pl: "🇵🇱",
  ru: "🇷🇺",
  ja: "🇯🇵",
  ko: "🇰🇷",
  zh: "🇨🇳",
  ar: "🇸🇦",
};

const stageVariant = (stage: string): "success" | "warning" | "info" | "error" => {
  switch (stage) {
    case "COMPLETE":
    case "DELIVERED":
      return "success";
    case "AWAITING_REVIEW":
    case "AWAITING_APPROVAL":
      return "warning";
    case "FAILED":
      return "error";
    default:
      return "info";
  }
};

const formatStage = (stage: string): string => {
  return stage
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
};

const formatDuration = (seconds?: number): string => {
  if (!seconds) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};

const stripExtension = (value?: string): string => {
  if (!value) return "";
  return value.replace(/\.[^/.]+$/, "");
};

// Extract client name from source path (e.g., /01_AUTO_PILOT/CBN/... → "CBN")
const extractClientFromPath = (path?: string): string | null => {
  if (!path) return null;
  // Common patterns: /..../CLIENT/..., or filename starts with client prefix like CBN, I2251
  const parts = path.split("/").filter(Boolean);

  // Look for known client patterns
  const knownClients = ["CBN", "TBN", "HOPE", "DAYSTAR", "GOD_TV", "BYU"];
  for (const part of parts) {
    const upper = part.toUpperCase();
    if (knownClients.some(c => upper.includes(c))) {
      return part.replace(/_/g, " ");
    }
  }

  // Fallback: parse from filename prefix like "CBNJD010126..."
  const filename = parts[parts.length - 1] || "";
  if (filename.startsWith("CBN")) return "CBN";
  if (filename.startsWith("TBN")) return "TBN";
  if (filename.startsWith("I225")) return "CBN Europe"; // I2251, I2252 pattern

  return null;
};

const trackLabel = (track: Track): string => {
  const typeLabel = track.type === "dub" ? "Dub" : "Sub";
  const flag = FLAG_MAP[track.language_code?.toLowerCase()] || "🌐";
  return `${flag} ${track.language_name} ${typeLabel}`;
};

const TranslationStepper = ({ track, showStatus = true }: { track: Track; showStatus?: boolean }) => {
  const status = (track.status || "").toLowerCase();
  const progress = track.progress || 0;

  // Determine active step (1=Translate, 2=Review/Polish)
  let activeStep = 1;
  if (status.includes("chief") || status.includes("review") || progress >= 60) activeStep = 2;

  // If complete/delivered/burning/reviewed, all translation steps are done
  if (["COMPLETE", "DELIVERED", "FINALIZED", "BURNING", "REVIEWED", "APPROVED"].includes(track.stage)) activeStep = 3;

  const steps = [
    { id: 1, label: "Translate", icon: "🌐" },
    { id: 2, label: "Review/Polish", icon: "🕵️" },
  ];

  return (
    <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(0,0,0,0.2)", borderRadius: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", position: "relative" }}>
        {/* Connecting Line */}
        <div style={{ position: "absolute", top: 12, left: 20, right: 20, height: 2, background: "rgba(255,255,255,0.1)", zIndex: 0 }} />

        {steps.map((step) => {
          const isActive = activeStep === step.id;
          const isDone = activeStep > step.id;
          return (
            <div key={step.id} style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{
                width: 24, height: 24, borderRadius: "50%",
                background: isDone ? "#22c55e" : isActive ? "#3b82f6" : "#27272a",
                border: `2px solid ${isActive ? "#60a5fa" : "transparent"}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, color: "#fff", transition: "all 0.3s ease"
              }}>
                {isDone ? "✓" : isActive ? <div className="animate-pulse w-2 h-2 bg-white rounded-full" /> : step.id}
              </div>
              <span style={{ fontSize: 10, fontWeight: isActive ? 600 : 400, color: isActive ? "#f5f5f5" : "#71717a" }}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>
      {showStatus && activeStep < 3 && (
        <div style={{ marginTop: 8, textAlign: "center", fontSize: 11, color: "#a1a1aa" }}>
          <span className="animate-pulse">●</span> {track.status || "Processing..."}
        </div>
      )}
    </div>
  );
};

export default function ProgramDetailView({ programId }: Props) {
  const { clearSelection } = useNavigation();
  const router = useRouter();
  const { programs, fetchPrograms, sendTrackToReview, approveTrack } = useProgramsStore();
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [isAddTrackOpen, setIsAddTrackOpen] = useState(false);
  const [selectedTrack, setSelectedTrack] = useState<Track | null>(null);
  const [masterMap, setMasterMap] = useState<Record<string, MasterSummary>>({});
  const masterFailures = useRef<Set<string>>(new Set());
  const [notifyEnabled, setNotifyEnabled] = useState(false);
  const [notifyEmail, setNotifyEmail] = useState("");
  const [notifyLink, setNotifyLink] = useState("");

  const { subscribe } = useSSEContext();

  const program = programs.find((p) => p.id === programId);
  const tracks = program?.tracks || [];

  useEffect(() => {
    if (!program) {
      fetchPrograms();
    }
  }, [program, fetchPrograms]);

  // SSE subscriptions for real-time updates
  useEffect(() => {
    const unsubs = [
      subscribe("programs_updated", () => fetchPrograms()),
      subscribe("tracks_updated", () => fetchPrograms()),
      subscribe("track_progress", () => fetchPrograms()),
    ];
    return () => unsubs.forEach(fn => fn());
  }, [subscribe, fetchPrograms]);

  useEffect(() => {
    if (!tracks.length) return;
    const masterIds = Array.from(
      new Set(
        tracks
          .map((track) => track.master_script_id)
          .filter((id): id is string => Boolean(id))
      )
    );

    const missing = masterIds.filter(
      (id) => !masterMap[id] && !masterFailures.current.has(id)
    );
    if (missing.length === 0) return;

    let cancelled = false;
    const fetchMasters = async () => {
      const results = await Promise.all(
        missing.map(async (id) => {
          try {
            const res = await fetch(`${API_BASE}/api/v2/master-scripts/${id}`);
            const data = await res.json();
            if (!res.ok) {
              masterFailures.current.add(id);
              return null;
            }
            return data as MasterSummary;
          } catch {
            masterFailures.current.add(id);
            return null;
          }
        })
      );

      if (cancelled) return;

      setMasterMap((prev) => {
        const next = { ...prev };
        results.forEach((item) => {
          if (item?.id) {
            next[item.id] = item;
          }
        });
        return next;
      });
    };

    fetchMasters();
    return () => {
      cancelled = true;
    };
  }, [tracks, masterMap]);

  if (!program) {
    return (
      <Modal onClose={clearSelection}>
        <div className="program-detail">
          <div className="loading-spinner" />
        </div>
      </Modal>
    );
  }

  const reviewTargets = tracks.filter((track) => track.stage === "AWAITING_REVIEW");
  const approvalTargets = tracks.filter((track) => track.stage === "AWAITING_APPROVAL");
  const failedTargets = tracks.filter((track) => track.stage === "FAILED" || track.status?.toLowerCase().includes("error"));

  const attentionCount = reviewTargets.length + approvalTargets.length + failedTargets.length;
  const needsAttention = program.needs_attention || failedTargets.length > 0;
  const attentionLabel = needsAttention ? "Needs Attention" : attentionCount > 0 ? "Needs Action" : "On Track";
  const attentionVariant = needsAttention ? "error" : attentionCount > 0 ? "warning" : "success";

  const avgProgress = tracks.length
    ? tracks.reduce((sum, track) => sum + (Number.isFinite(track.progress) ? track.progress : 0), 0) / tracks.length
    : 0;
  const progressPercent = Math.round(Math.min(100, Math.max(0, avgProgress)));
  const completedCount = tracks.filter((track) => ["COMPLETE", "DELIVERED"].includes(track.stage)).length;

  const primaryReview = reviewTargets[0];
  const primaryApproval = approvalTargets[0];

  // Find best track for editor: prioritize COMPLETED > REVIEWED > any track with job_id
  const completedTrack = tracks.find((track) => track.stage === "COMPLETED" && track.job_id);
  const reviewedTrack = tracks.find((track) => track.stage === "REVIEWED" && track.job_id);
  const anyTrack = tracks.find((track) => track.job_id);

  const editorTarget =
    primaryReview?.job_id ||
    primaryApproval?.job_id ||
    completedTrack?.job_id ||
    reviewedTrack?.job_id ||
    anyTrack?.job_id ||
    stripExtension(program.original_filename) ||
    program.id;

  const actionQueue = [
    ...reviewTargets.map((track) => ({ track, action: "review" as const, label: "Send to Reviewer" })),
    ...approvalTargets.map((track) => ({ track, action: "approve" as const, label: "Approve Burn" })),
  ];

  const streamId = stripExtension(program.original_filename) || program.id;
  const posterUrl = program.thumbnail_path ? `${API_BASE}/api/v2/thumbnails/${program.id}` : undefined;

  const handleOpenEditor = () => {
    if (!editorTarget) return;
    router.push(`/editor/${encodeURIComponent(editorTarget)}`);
  };

  const handleSendToReview = async (trackId: string) => {
    setActionBusy(trackId);
    await sendTrackToReview(trackId);
    setActionBusy(null);
  };

  const handleApprove = async (trackId: string) => {
    setActionBusy(trackId);
    await approveTrack(trackId);
    setActionBusy(null);
  };

  return (
    <Modal onClose={clearSelection}>
      <div className="program-detail">
        <header className="detail-header">
          <div>
            <div className="detail-title">{program.title}</div>
            <div className="page-subtitle">
              {program.client && program.client !== "unknown"
                ? program.client
                : extractClientFromPath(program.video_path) || "Localization Overview"}
            </div>
          </div>
          <Button variant="ghost" onClick={clearSelection}>
            ← Back
          </Button>
        </header>

        <div className="detail-content">
          <section className="detail-main">
            <div className="detail-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Source Preview</div>
                  <div className="panel-subtitle">{program.original_filename || "Program Source"}</div>
                </div>
                <Badge label={attentionLabel} variant={attentionVariant} />
              </div>
              <div className="media-shell">
                {program.video_path ? (
                  <video
                    className="video-preview"
                    src={`${API_BASE}/api/stream/${streamId}`}
                    controls
                    poster={posterUrl}
                  />
                ) : (
                  <div className="video-preview-placeholder">
                    <span style={{ fontSize: "32px" }}>Preview</span>
                    <div>No video attached</div>
                  </div>
                )}
              </div>
            </div>

            <div className="detail-panel timeline-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Timeline</div>
                  <div className="panel-subtitle">Progress {progressPercent}%</div>
                </div>
                <div className="panel-subtitle">{formatDuration(program.duration_seconds)}</div>
              </div>
              <div className="timeline-track">
                <div className="timeline-progress" style={{ width: `${progressPercent}%` }} />
              </div>
              <div className="waveform">
                {WAVEFORM_BARS.map((height, index) => (
                  <span key={`${height}-${index}`} className="waveform-bar" style={{ height: `${height}%` }} />
                ))}
              </div>
              <div className="timeline-meta">
                <span>
                  {completedCount}/{tracks.length} delivered
                </span>
                <span>Avg progress {progressPercent}%</span>
              </div>
            </div>

            <div className="detail-panel">
              <div className="panel-title" style={{ marginBottom: "12px" }}>
                Program Details
              </div>
              <div className="info-grid">
                <div className="info-cell">
                  <span className="info-label">Client</span>
                  <span>{program.client && program.client !== "unknown"
                    ? program.client
                    : extractClientFromPath(program.video_path) || "—"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Duration</span>
                  <span>{formatDuration(program.duration_seconds)}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Due Date</span>
                  <span>{program.due_date || "—"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Style</span>
                  <span>{program.default_style || "Classic"}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Tracks</span>
                  <span>{completedCount} delivered of {tracks.length}</span>
                </div>
                <div className="info-cell">
                  <span className="info-label">Updated</span>
                  <span>{new Date(program.updated_at).toLocaleString()}</span>
                </div>
              </div>
            </div>
          </section>

          <aside className="detail-side">
            <div className="detail-panel action-panel">
              <div className="panel-header">
                <div>
                  <div className="panel-title">Command Center</div>
                  <div className="panel-subtitle">Next actions and escalations</div>
                </div>
                <Badge label={attentionLabel} variant={attentionVariant} />
              </div>

              <div className="action-stack">
                <Button variant="primary" onClick={handleOpenEditor} disabled={!editorTarget}>
                  Open Editor
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => primaryReview && handleSendToReview(primaryReview.id)}
                  disabled={!primaryReview || actionBusy === primaryReview.id}
                >
                  Send to Reviewer
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => primaryApproval && handleApprove(primaryApproval.id)}
                  disabled={!primaryApproval || actionBusy === primaryApproval.id}
                >
                  Approve Burn
                </Button>
              </div>

              <div className="action-metrics">
                <div className="metric-card">
                  <div className="metric-label">Needs Review</div>
                  <div className="metric-value">{reviewTargets.length}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Awaiting Approval</div>
                  <div className="metric-value">{approvalTargets.length}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Blocked</div>
                  <div className="metric-value">{failedTargets.length}</div>
                </div>
              </div>

              <div className="action-list">
                {actionQueue.length > 0 ? (
                  actionQueue.map((item) => (
                    <div key={`${item.action}-${item.track.id}`} className="action-item">
                      <div className="action-info">
                        <div className="action-title">{trackLabel(item.track)}</div>
                        <div className="action-subtitle">
                          {formatStage(item.track.stage)} • {item.track.status || "Queued"}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        onClick={() =>
                          item.action === "review"
                            ? handleSendToReview(item.track.id)
                            : handleApprove(item.track.id)
                        }
                        disabled={actionBusy === item.track.id}
                      >
                        {item.label}
                      </Button>
                    </div>
                  ))
                ) : (
                  <div className="empty-state">
                    <p>No pending review or approval.</p>
                  </div>
                )}
              </div>
            </div>

            <div className="detail-panel">
              <div className="panel-header" style={{ marginBottom: "6px" }}>
                <div className="panel-title">Output Tracks</div>
                <Button variant="ghost" onClick={() => setIsAddTrackOpen(true)}>
                  + Add Track
                </Button>
              </div>
              <div className="card" style={{ padding: "10px 12px", marginBottom: "12px", display: "flex", flexDirection: "column", gap: "8px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
                  <input
                    type="checkbox"
                    checked={notifyEnabled}
                    onChange={(e) => setNotifyEnabled(e.target.checked)}
                    style={{ accentColor: "rgb(var(--omega-blue))" }}
                  />
                  Notify client on delivery (optional)
                </label>
                <div style={{ display: "grid", gap: "8px", gridTemplateColumns: "minmax(160px, 1fr) minmax(200px, 2fr)" }}>
                  <input
                    type="email"
                    className="input"
                    placeholder="client@example.com"
                    value={notifyEmail}
                    onChange={(e) => setNotifyEmail(e.target.value)}
                    disabled={!notifyEnabled}
                  />
                  <input
                    type="text"
                    className="input"
                    placeholder="https://download.link/video"
                    value={notifyLink}
                    onChange={(e) => setNotifyLink(e.target.value)}
                    disabled={!notifyEnabled}
                  />
                </div>
                <span style={{ fontSize: "11px", color: "rgb(var(--omega-text-3))" }}>
                  {notifyEnabled
                    ? "Provide email + link to include in delivery notification."
                    : "Enable to send a delivery email with the download link."}
                </span>
              </div>
              <div className="tracks-section">
                {selectedTrack ? (
                  <TrackDetailPanel
                    track={selectedTrack}
                    onClose={() => setSelectedTrack(null)}
                  />
                ) : tracks.length > 0 ? (
                  tracks.map((track) => (
                    <TrackCard
                      key={track.id}
                      track={track}
                      masterScript={track.master_script_id ? masterMap[track.master_script_id] : undefined}
                      notify={{
                        enabled: notifyEnabled,
                        email: notifyEmail,
                        link: notifyLink,
                        programTitle: program?.title,
                      }}
                      onSelect={() => setSelectedTrack(track)}
                    />
                  ))
                ) : (
                  <div className="empty-state">
                    <p>No tracks yet</p>
                    <Button variant="primary" onClick={() => setIsAddTrackOpen(true)}>
                      Add Track
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </aside>
        </div>
        <AddTrackModal
          open={isAddTrackOpen}
          programId={program.id}
          onClose={() => setIsAddTrackOpen(false)}
        />
      </div>
    </Modal>
  );
}

interface TrackCardProps {
  track: Track;
  masterScript?: MasterSummary;
  notify?: {
    enabled: boolean;
    email: string;
    link: string;
    programTitle?: string;
  };
  onSelect?: () => void;
}

function TrackCard({ track, masterScript, notify, onSelect }: TrackCardProps) {
  const { startDubbing, recordDelivery, approveTrack, revealFile } = useProgramsStore();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleStartDub = async () => {
    setBusy(true);
    await startDubbing(track.id);
    setBusy(false);
  };

  const handleDeliver = async () => {
    // Simple mock delivery flow for now
    const method = "folder";
    setBusy(true);
    const notifyReady =
      notify?.enabled &&
      notify.email.trim().length > 0 &&
      notify.link.trim().length > 0;
    await recordDelivery(
      track.id,
      method,
      "Client Folder",
      "Manual delivery via UI",
      notifyReady
        ? {
          notifyEmail: notify.email.trim(),
          deliveryLink: notify.link.trim(),
          programTitle: notify.programTitle,
        }
        : undefined
    );
    setBusy(false);
  };

  const handleProvisionalDeliver = async () => {
    const method = "folder";
    setBusy(true);
    const notifyReady =
      notify?.enabled &&
      notify.email.trim().length > 0 &&
      notify.link.trim().length > 0;
    await recordDelivery(
      track.id,
      method,
      "Client Folder",
      "Provisional delivery via UI",
      notifyReady
        ? {
          provisional: true,
          notifyEmail: notify.email.trim(),
          deliveryLink: notify.link.trim(),
          programTitle: notify.programTitle,
        }
        : { provisional: true }
    );
    setBusy(false);
  };

  const handleReburn = async () => {
    // Direct action for now to bypass browser automation issues with native confirm
    setBusy(true);
    await approveTrack(track.id);
    setBusy(false);
  };

  const handleReveal = async (fileType: "video" | "srt") => {
    if (!track.id) return;
    setBusy(true);
    await revealFile(track.id, fileType);
    setBusy(false);
  };

  const isDubTrack = track.type === "dub";
  const canDub = isDubTrack && (track.stage === "QUEUED" || track.stage === "FAILED");
  const canDeliver = ["COMPLETE", "COMPLETED"].includes(track.stage);
  const canReburn = !isDubTrack && ["COMPLETE", "COMPLETED"].includes(track.stage);
  const masterLocked = Boolean(masterScript?.locked_at || masterScript?.state === "locked");
  const hasSrt = Boolean(track.srt_path);
  const hasVideo = Boolean(track.video_path);

  return (
    <div className="track-card">
      <div
        className="track-meta"
        onClick={() => onSelect ? onSelect() : setExpanded(!expanded)}
        style={{ cursor: 'pointer' }}
      >
        <div className="track-title">
          {trackLabel(track)}
          {track.voice_id && <span className="voice-label"> • Voice: {track.voice_id}</span>}
        </div>
        <div className="track-status">
          {formatStage(track.stage)}
          {track.status && track.status !== "Pending" && !["TRANSLATING_CLOUD", "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "REVIEWED", "FINALIZING", "BURNING", "COMPLETE", "DELIVERED", "FINALIZED"].includes(track.stage) && (
            <span className="status-detail"> — {track.status}</span>
          )}
        </div>

        {/* Use Stepper for Cloud Translation Pipeline */}
        {/* Use Stepper for Cloud Translation Pipeline */}
        {["TRANSLATING_CLOUD", "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "TRANSLATING_CLOUD_SUBMITTED", "REVIEWED", "FINALIZING", "BURNING", "COMPLETE", "DELIVERED", "FINALIZED", "APPROVED"].includes(track.stage) ? (
          <>
            <TranslationStepper
              track={track}
              showStatus={!["CLOUD_TRANSLATING", "CLOUD_REVIEWING", "BURNING", "FINALIZING"].includes(track.stage)}
            />
            {/* Show Progress Bar during active processing */}
            {["CLOUD_TRANSLATING", "CLOUD_REVIEWING", "BURNING", "FINALIZING"].includes(track.stage) && (
              <div style={{ marginTop: 8 }}>
                <ProgressBar
                  value={track.progress}
                  label={
                    <span className="flex items-center gap-2">
                      {track.stage.includes("CLOUD") && <span className="animate-pulse text-green-400">●</span>}
                      {track.status || "Processing..."}
                    </span>
                  }
                />
              </div>
            )}
          </>
        ) : (
          <ProgressBar value={track.progress} label={`${track.language_name} progress`} />
        )}
      </div>
      <div className="track-actions">
        <div className="track-output-links">
          <div className="track-output-label">Outputs</div>
          <div className="track-output-buttons">
            {hasSrt ? (
              <Button variant="ghost" onClick={() => handleReveal("srt")} disabled={busy}>
                Open SRT
              </Button>
            ) : (
              <span className="track-output-muted">SRT pending</span>
            )}
            {hasVideo ? (
              <Button variant="ghost" onClick={() => handleReveal("video")} disabled={busy}>
                Open Video
              </Button>
            ) : (
              <span className="track-output-muted">Video pending</span>
            )}
          </div>
        </div>
        {/* Version Badge */}
        {track.output_version && (
          <span
            className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30"
            title="Output Version"
          >
            v{track.output_version}
          </span>
        )}
        {/* Pending Resync Indicator */}
        {track.pending_resync && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse"
            title="Pending resync with master script - changes may be required"
          >
            ⟳ Resync
          </span>
        )}
        {/* Override Indicator */}
        {track.output_override && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30"
            title={`Override: ${track.override_reason || 'Output-only fix'}`}
          >
            ✎ Override
          </span>
        )}
        {/* Locked Badge */}
        {track.locked_at && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
            title={`Locked${track.locked_by ? ` by ${track.locked_by}` : ''}`}
          >
            🔒 Locked
          </span>
        )}
        {masterLocked && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30"
            title={`Master locked${masterScript?.locked_by ? ` by ${masterScript.locked_by}` : ''}`}
          >
            🔒 Master
          </span>
        )}
        <Badge label={formatStage(track.stage)} variant={stageVariant(track.stage)} />

        {canDub && (
          <Button variant="secondary" onClick={handleStartDub} disabled={busy}>
            {busy ? "Starting..." : "Start Dub"}
          </Button>
        )}

        {canReburn && (
          <Button variant="secondary" onClick={handleReburn} disabled={busy} title="Regenerate verified video">
            {busy ? "Queuing..." : "Re-burn"}
          </Button>
        )}

        {canDeliver && (
          <>
            <Button variant="ghost" onClick={handleDeliver} disabled={busy}>
              {busy ? "Delivering..." : "Deliver"}
            </Button>
            <Button variant="secondary" onClick={handleProvisionalDeliver} disabled={busy}>
              {busy ? "Delivering..." : "Provisional"}
            </Button>
          </>
        )}
      </div>
      {expanded && (
        <TrackDetailPanel track={track} onClose={() => setExpanded(false)} />
      )}
    </div>
  );
}
