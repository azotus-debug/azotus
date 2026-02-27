"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Badge from "@/components/common/Badge";
import Button from "@/components/common/Button";
import AddTrackModal from "@/components/common/AddTrackModal";
import Modal from "@/components/common/Modal";
import ProgressBar from "@/components/common/ProgressBar";
import TrackDetailPanel from "@/components/common/TrackDetailPanel";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore, Track } from "@/store/programs";
import { useRealtime } from "@/contexts/RealtimeContext";

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

  const { socket, isConnected } = useRealtime();

  const program = programs.find((p) => p.id === programId);
  const tracks = useMemo(() => program?.tracks ?? [], [program?.tracks]);

  useEffect(() => {
    if (!program) {
      fetchPrograms();
    }
  }, [program, fetchPrograms]);

  // Real-time WebSocket subscriptions
  useEffect(() => {
    if (!socket || !isConnected) return;

    const handleUpdate = (data: unknown) => {
      console.log('Realtime track update received:', data);
      fetchPrograms(); // For now, we just trigger a refetch of the store. Next step is optimistic updates.
    };

    socket.on('track_updated', handleUpdate);

    // If we wanted to scope, we could emit subscribe_to_program here

    return () => {
      socket.off('track_updated', handleUpdate);
    };
  }, [socket, isConnected, fetchPrograms]);

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

        <div className="detail-content" style={{ display: "flex", flexDirection: "column", gap: "24px" }}>

          {/* Top Context Header Area */}
          <section className="context-header" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 2fr)", gap: "24px", alignItems: "stretch" }}>

            {/* Left: Compact Video Preview */}
            <div className="detail-panel" style={{ display: "flex", flexDirection: "column" }}>
              <div className="panel-header" style={{ marginBottom: "8px" }}>
                <div>
                  <div className="panel-title">Source Preview</div>
                </div>
                <Badge label={attentionLabel} variant={attentionVariant} />
              </div>
              <div className="media-shell" style={{ flex: 1, minHeight: "200px" }}>
                {program.video_path ? (
                  <video
                    className="video-preview"
                    src={`${API_BASE}/api/stream/${streamId}`}
                    controls
                    poster={posterUrl}
                    style={{ height: "100%", width: "100%", objectFit: "cover", borderRadius: "8px" }}
                  />
                ) : (
                  <div className="video-preview-placeholder" style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                    <span style={{ fontSize: "24px" }}>Preview</span>
                    <div style={{ fontSize: "12px" }}>No video attached</div>
                  </div>
                )}
              </div>
            </div>

            {/* Right: Program Context & Command Center */}
            <div className="detail-panel" style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>

              <div>
                <div className="panel-header" style={{ marginBottom: "16px" }}>
                  <div>
                    <div className="panel-title">Program Info</div>
                    <div className="panel-subtitle">Duration: {formatDuration(program.duration_seconds)}</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div className="text-sm font-semibold">{completedCount} / {tracks.length}</div>
                    <div className="text-xs text-muted">Tracks Delivered</div>
                  </div>
                </div>

                <div className="info-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: "16px" }}>
                  <div className="info-cell">
                    <span className="info-label">Client</span>
                    <span className="truncate">{program.client && program.client !== "unknown" ? program.client : extractClientFromPath(program.video_path) || "—"}</span>
                  </div>
                  <div className="info-cell">
                    <span className="info-label">Due Date</span>
                    <span className="truncate">{program.due_date || "—"}</span>
                  </div>
                  <div className="info-cell">
                    <span className="info-label">Style</span>
                    <span className="truncate">{program.default_style || "Classic"}</span>
                  </div>
                </div>

                <div className="mt-6 mb-4">
                  <div className="flex justify-between text-xs text-muted mb-2">
                    <span>Overall Pipeline Progress</span>
                    <span>{progressPercent}%</span>
                  </div>
                  <div className="progress-track w-full">
                    <div className="progress-fill" style={{ width: `${progressPercent}%` }} />
                  </div>
                </div>
              </div>

              {/* Command Center Action Stack inside Info Panel */}
              <div className="action-stack" style={{ display: "flex", gap: "12px", marginTop: "16px" }}>
                <Button variant="primary" onClick={handleOpenEditor} disabled={!editorTarget}>
                  Open Editor
                </Button>
                {primaryReview && (
                  <Button
                    variant="secondary"
                    onClick={() => handleSendToReview(primaryReview.id)}
                    disabled={actionBusy === primaryReview.id}
                  >
                    Send to Reviewer
                  </Button>
                )}
                {primaryApproval && (
                  <Button
                    variant="ghost"
                    onClick={() => handleApprove(primaryApproval.id)}
                    disabled={actionBusy === primaryApproval.id}
                  >
                    Approve Burn
                  </Button>
                )}
              </div>
            </div>
          </section>

          {/* Dominant Output Tracks Area */}
          <section className="mt-4 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="flex items-center justify-between mb-5">
              <div>
                <div className="text-base font-medium text-gray-100 mb-1">Output Tracks</div>
                <div className="text-sm text-muted">Manage deliverables and workflows for this program</div>
              </div>
              <Button variant="ghost" onClick={() => setIsAddTrackOpen(true)}>
                + Add Track
              </Button>
            </div>

            {/* Optional Notification configuration inside Tracks container */}
            <div className="flex flex-wrap gap-4 items-center p-3 mb-5 rounded-lg" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px", minWidth: "200px" }}>
                <input
                  type="checkbox"
                  checked={notifyEnabled}
                  onChange={(e) => setNotifyEnabled(e.target.checked)}
                  style={{ accentColor: "rgb(var(--omega-blue))" }}
                />
                Notify client on delivery?
              </label>
              <div style={{ display: "flex", gap: "12px", flex: 1 }}>
                <input
                  type="email"
                  className="input flex-1"
                  placeholder="client@example.com"
                  value={notifyEmail}
                  onChange={(e) => setNotifyEmail(e.target.value)}
                  disabled={!notifyEnabled}
                  style={{ maxWidth: "300px" }}
                />
                <input
                  type="text"
                  className="input flex-2"
                  placeholder="https://download.link/video"
                  value={notifyLink}
                  onChange={(e) => setNotifyLink(e.target.value)}
                  disabled={!notifyEnabled}
                />
              </div>
            </div>

            <div className="tracks-section" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(400px, 1fr))", gap: "16px", alignItems: "start" }}>
              {selectedTrack ? (
                <div style={{ gridColumn: "1 / -1" }}>
                  <TrackDetailPanel
                    track={selectedTrack}
                    onClose={() => setSelectedTrack(null)}
                  />
                </div>
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
                <div className="empty-state" style={{ gridColumn: "1 / -1", padding: "40px" }}>
                  <p>No tracks yet</p>
                  <Button variant="primary" onClick={() => setIsAddTrackOpen(true)} style={{ marginTop: "16px" }}>
                    Create First Track
                  </Button>
                </div>
              )}
            </div>
          </section>

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
    <div
      className={`flex flex-col p-4 mb-3 rounded-xl transition-all relative overflow-hidden group`}
      style={{
        background: expanded ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.02)",
        border: expanded ? "1px solid rgba(var(--omega-blue), 0.5)" : "1px solid rgba(255,255,255,0.05)",
        boxShadow: expanded ? "0 0 0 1px rgba(var(--omega-blue), 0.2)" : "0 4px 12px rgba(0,0,0,0.1)"
      }}
    >
      <div className="flex items-center justify-between gap-6">
        {/* Left Side: Overview & Progress */}
        <div className="flex flex-col flex-1 min-w-0 pr-4 cursor-pointer gap-2" onClick={() => onSelect ? onSelect() : setExpanded(!expanded)}>
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2.5">
              <span className="text-[15px] font-medium text-gray-100 truncate">{trackLabel(track)}</span>
              <Badge label={formatStage(track.stage)} variant={stageVariant(track.stage)} />
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              {Boolean(track.output_version) && (
                <span className="text-[10px] font-mono font-medium rounded px-1.5 py-0.5" style={{ color: "rgb(147, 197, 253)", backgroundColor: "rgba(59, 130, 246, 0.1)", border: "1px solid rgba(59, 130, 246, 0.2)" }} title="Output Version">v{track.output_version}</span>
              )}
              {Boolean(track.pending_resync) && (
                <span className="text-[10px] font-medium rounded px-1.5 py-0.5 animate-pulse" style={{ color: "rgb(252, 211, 77)", backgroundColor: "rgba(245, 158, 11, 0.1)", border: "1px solid rgba(245, 158, 11, 0.2)" }}>⟳ Resync</span>
              )}
              {Boolean(track.output_override) && (
                <span className="text-[10px] font-medium rounded px-1.5 py-0.5" style={{ color: "rgb(216, 180, 254)", backgroundColor: "rgba(168, 85, 247, 0.1)", border: "1px solid rgba(168, 85, 247, 0.2)" }}>✎ Override</span>
              )}
              {Boolean(track.locked_at) && (
                <span className="text-[10px] font-medium rounded px-1.5 py-0.5" style={{ color: "rgb(110, 231, 183)", backgroundColor: "rgba(16, 185, 129, 0.1)", border: "1px solid rgba(16, 185, 129, 0.2)" }}>🔒 Locked</span>
              )}
              {Boolean(masterLocked) && (
                <span className="text-[10px] font-medium rounded px-1.5 py-0.5" style={{ color: "rgb(125, 211, 252)", backgroundColor: "rgba(14, 165, 233, 0.1)", border: "1px solid rgba(14, 165, 233, 0.2)" }}>🔒 Master</span>
              )}
              {Boolean(track.voice_id) && <span className="text-[11px] text-muted truncate max-w-[150px]">• {track.voice_id}</span>}
            </div>
          </div>

          {!["PENDING", "COMPLETE", "COMPLETED", "DELIVERED", "FINALIZED", "APPROVED"].includes(track.stage) && (
            <div className="w-full max-w-lg mt-2">
              {["TRANSLATING_CLOUD", "CLOUD_TRANSLATING", "CLOUD_REVIEWING", "TRANSLATING_CLOUD_SUBMITTED", "REVIEWED", "FINALIZING", "BURNING"].includes(track.stage) ? (
                <>
                  <TranslationStepper track={track} showStatus={!["CLOUD_TRANSLATING", "CLOUD_REVIEWING", "BURNING", "FINALIZING"].includes(track.stage)} />
                  {["CLOUD_TRANSLATING", "CLOUD_REVIEWING", "BURNING", "FINALIZING"].includes(track.stage) && (
                    <div className="mt-2">
                      <ProgressBar value={track.progress} label={<span className="flex items-center gap-2">{track.stage.includes("CLOUD") && <span className="animate-pulse text-emerald-400">●</span>}{track.status || "Processing..."}</span>} />
                    </div>
                  )}
                </>
              ) : (
                <ProgressBar value={track.progress} label={`${track.language_name} progress`} />
              )}
            </div>
          )}
        </div>

        {/* Right Side: Actions Core */}
        <div className="flex items-center gap-3 shrink-0 ml-4 py-1">

          {(hasSrt || hasVideo) && (
            <div className="flex items-center rounded-md p-1 shadow-sm h-8" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.05)" }}>
              {hasSrt && (
                <button className="px-3 h-full flex items-center text-xs font-medium text-gray-400 hover:text-white hover:bg-white/10 rounded-sm transition-colors" onClick={(e) => { e.stopPropagation(); handleReveal("srt"); }} disabled={busy}>SRT</button>
              )}
              {hasSrt && hasVideo && <div className="w-px h-4 bg-white/10 mx-1" />}
              {hasVideo && (
                <button className="px-3 h-full flex items-center text-xs font-medium text-gray-400 hover:text-white hover:bg-white/10 rounded-sm transition-colors" onClick={(e) => { e.stopPropagation(); handleReveal("video"); }} disabled={busy}>Video</button>
              )}
            </div>
          )}

          {(canDub || canReburn || canDeliver) && (
            <div className="flex items-center gap-2 h-8">
              {canDub && (
                <button className="px-4 h-full flex items-center text-xs font-medium text-white rounded-md transition-colors shadow-sm hover:brightness-110" style={{ background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.05)" }} onClick={(e) => { e.stopPropagation(); handleStartDub(); }} disabled={busy}>
                  Start Dub
                </button>
              )}

              {canReburn && (
                <button className="px-4 h-full flex items-center text-xs font-medium text-gray-300 rounded-md transition-colors hover:bg-white/5" style={{ background: "transparent", border: "1px solid rgba(255,255,255,0.08)" }} onClick={(e) => { e.stopPropagation(); handleReburn(); }} disabled={busy}>
                  Re-burn
                </button>
              )}

              {canDeliver && (
                <div className="flex items-center rounded-md p-1 shadow-sm h-full" style={{ backgroundColor: "rgba(16, 185, 129, 0.1)", border: "1px solid rgba(16, 185, 129, 0.2)" }}>
                  <button className="px-3 h-full flex items-center text-xs font-medium text-emerald-400 hover:text-emerald-300 rounded-sm transition-colors" style={{ backgroundColor: "transparent" }} onClick={(e) => { e.stopPropagation(); handleProvisionalDeliver(); }} disabled={busy}>
                    Provisional
                  </button>
                  <div className="w-px h-4 mx-1" style={{ backgroundColor: "rgba(16, 185, 129, 0.2)" }} />
                  <button className="px-3 h-full flex items-center text-xs font-medium text-emerald-400 hover:text-emerald-300 rounded-sm transition-colors shadow-sm" style={{ backgroundColor: "rgba(16, 185, 129, 0.1)" }} onClick={(e) => { e.stopPropagation(); handleDeliver(); }} disabled={busy}>
                    Deliver
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {expanded && (
        <div className="mt-4 pt-4 border-t border-white/10">
          <TrackDetailPanel track={track} onClose={() => setExpanded(false)} />
        </div>
      )}
    </div>
  );
}
