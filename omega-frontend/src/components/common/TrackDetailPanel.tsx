"use client";

import { useState, useEffect } from "react";
import { Track, useProgramsStore } from "@/store/programs";
import Button from "@/components/common/Button";
import Badge from "@/components/common/Badge";
import ProgressBar from "@/components/common/ProgressBar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

interface TrackDetailPanelProps {
  track: Track;
  onClose: () => void;
}

export default function TrackDetailPanel({ track: initialTrack, onClose }: TrackDetailPanelProps) {
  const { revealFile, approveTrack } = useProgramsStore();
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [track, setTrack] = useState(initialTrack);

  // Remote review modal state
  const [showReviewModal, setShowReviewModal] = useState(false);
  const [reviewEmail, setReviewEmail] = useState("");
  const [reviewSending, setReviewSending] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewSuccess, setReviewSuccess] = useState<string | null>(null);

  const handleSendReview = async () => {
    if (!reviewEmail) return;
    setReviewSending(true);
    setReviewError(null);
    setReviewSuccess(null);

    try {
      const res = await fetch(`${API_BASE}/api/v2/tracks/${track.id}/send-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: reviewEmail })
      });

      const data = await res.json();
      if (res.ok) {
        setReviewSuccess(data.message || "Review link sent!");
        setTimeout(() => {
          setShowReviewModal(false);
          setReviewEmail("");
          setReviewSuccess(null);
        }, 2000);
      } else {
        setReviewError(data.error || "Failed to send review");
      }
    } catch {
      setReviewError("Network error");
    } finally {
      setReviewSending(false);
    }
  };

  // Poll for updates during active stages
  const isActive = [
    "BURNING",
    "FINALIZING",
    "INGESTING",
    "TRANSCRIBING",
    "TRANSLATING",
    "TRANSLATING_CLOUD_SUBMITTED",
    "CLOUD_TRANSLATING",
    "CLOUD_REVIEWING",
    "CLOUD_EDITING",
  ].includes(track.stage);

  useEffect(() => {
    if (!isActive) return;

    const pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v2/tracks/${track.id}`);
        if (res.ok) {
          const updated = await res.json();
          setTrack(updated);

          // Stop polling if stage changed to terminal state
          if (["COMPLETE", "COMPLETED", "DELIVERED", "FAILED"].includes(updated.stage)) {
            clearInterval(pollInterval);
          }
        }
      } catch (e) {
        console.error("Failed to poll track status:", e);
      }
    }, 3000);

    return () => clearInterval(pollInterval);
  }, [track.id, isActive]);

  const handleCopyPath = async (path: string, label: string) => {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    } catch (e) {
      console.error("Failed to copy:", e);
    }
  };

  const handleReveal = async (fileType: "video" | "srt") => {
    setBusy(true);
    await revealFile(track.id, fileType);
    setBusy(false);
  };

  const handleReburn = async () => {
    setBusy(true);
    await approveTrack(track.id);
    setBusy(false);
    onClose();
  };

  const isBurning = ["BURNING", "FINALIZING"].includes(track.stage);
  const isComplete = ["COMPLETE", "COMPLETED", "DELIVERED", "FINALIZED"].includes(track.stage);

  return (
    <div className="track-detail-panel">
      <div className="track-detail-header">
        <h3>
          {track.language_name} {track.type === "dub" ? "Dub" : "Sub"}
        </h3>
        <button className="close-btn" onClick={onClose}>×</button>
      </div>

      <div className="track-detail-content">
        {/* Status Section */}
        <div className="detail-section">
          <div className="detail-row">
            <span className="label">Status:</span>
            <Badge label={track.stage} variant={isComplete ? "success" : isBurning ? "warning" : "info"} />
          </div>
          {track.status && (
            <div className="detail-row">
              <span className="label">Message:</span>
              <span className="value">{track.status}</span>
            </div>
          )}
          {isBurning && (
            <div className="detail-progress">
              <ProgressBar value={track.progress} label={`${Math.round(track.progress || 0)}%`} />
            </div>
          )}
        </div>

        {/* Files Section */}
        <div className="detail-section">
          <div className="section-title">Output Files</div>

          {track.srt_path ? (
            <div className="file-row">
              <span className="file-type">📄 SRT</span>
              <span className="file-path" title={track.srt_path}>
                {track.srt_path.split("/").pop()}
              </span>
              <div className="file-actions">
                <button
                  className="icon-btn"
                  onClick={() => handleCopyPath(track.srt_path!, "srt")}
                  title="Copy path"
                >
                  {copied === "srt" ? "✓" : "📋"}
                </button>
                <button
                  className="icon-btn"
                  onClick={() => handleReveal("srt")}
                  disabled={busy}
                  title="Open in Finder"
                >
                  📂
                </button>
              </div>
            </div>
          ) : (
            <div className="file-row empty">
              <span className="file-type">📄 SRT</span>
              <span className="file-path muted">Not available</span>
            </div>
          )}

          {track.video_path ? (
            <div className="file-row">
              <span className="file-type">🎬 Video</span>
              <span className="file-path" title={track.video_path}>
                {track.video_path.split("/").pop()}
              </span>
              <div className="file-actions">
                <button
                  className="icon-btn"
                  onClick={() => handleCopyPath(track.video_path!, "video")}
                  title="Copy path"
                >
                  {copied === "video" ? "✓" : "📋"}
                </button>
                <button
                  className="icon-btn"
                  onClick={() => handleReveal("video")}
                  disabled={busy}
                  title="Open in Finder"
                >
                  📂
                </button>
              </div>
            </div>
          ) : (
            <div className="file-row empty">
              <span className="file-type">🎬 Video</span>
              <span className="file-path muted">{isBurning ? "Encoding..." : "Not available"}</span>
            </div>
          )}
        </div>

        {/* Actions Section */}
        {isComplete && (
          <div className="detail-section">
            <div className="section-title">Actions</div>
            <div className="action-buttons">
              <Button variant="secondary" onClick={handleReburn} disabled={busy}>
                {busy ? "Queuing..." : "🔄 Re-burn"}
              </Button>
              <Button variant="secondary" onClick={() => setShowReviewModal(true)} disabled={busy}>
                📧 Send for Review
              </Button>
            </div>
          </div>
        )}

        {/* Review Modal */}
        {showReviewModal && (
          <div className="modal-overlay" onClick={() => !reviewSending && setShowReviewModal(false)}>
            <div className="modal-content" onClick={e => e.stopPropagation()}>
              <h4>Send for Remote Review</h4>
              <p className="modal-description">
                A video preview will be uploaded and a review link sent to the reviewer.
              </p>
              <input
                type="email"
                placeholder="reviewer@example.com"
                value={reviewEmail}
                onChange={e => setReviewEmail(e.target.value)}
                disabled={reviewSending}
                className="email-input"
              />
              {reviewError && <p className="error-message">{reviewError}</p>}
              {reviewSuccess && <p className="success-message">{reviewSuccess}</p>}
              <div className="modal-actions">
                <Button variant="secondary" onClick={() => setShowReviewModal(false)} disabled={reviewSending}>
                  Cancel
                </Button>
                <Button variant="primary" onClick={handleSendReview} disabled={reviewSending || !reviewEmail}>
                  {reviewSending ? "Sending..." : "Send Review Link"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>

      <style jsx>{`
        .track-detail-panel {
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: 8px;
          margin-top: 12px;
          overflow: hidden;
        }
        .track-detail-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 16px;
          background: var(--color-surface-alt);
          border-bottom: 1px solid var(--color-border);
        }
        .track-detail-header h3 {
          margin: 0;
          font-size: 14px;
          font-weight: 600;
        }
        .close-btn {
          background: none;
          border: none;
          font-size: 20px;
          color: var(--color-text-muted);
          cursor: pointer;
        }
        .close-btn:hover {
          color: var(--color-text);
        }
        .track-detail-content {
          padding: 16px;
        }
        .detail-section {
          margin-bottom: 16px;
        }
        .detail-section:last-child {
          margin-bottom: 0;
        }
        .section-title {
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          color: var(--color-text-muted);
          margin-bottom: 8px;
        }
        .detail-row {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 6px;
        }
        .label {
          font-size: 12px;
          color: var(--color-text-muted);
          min-width: 60px;
        }
        .value {
          font-size: 12px;
        }
        .detail-progress {
          margin-top: 8px;
        }
        .file-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          background: var(--color-surface-alt);
          border-radius: 6px;
          margin-bottom: 6px;
        }
        .file-row.empty {
          opacity: 0.6;
        }
        .file-type {
          font-size: 12px;
          min-width: 60px;
        }
        .file-path {
          flex: 1;
          font-size: 12px;
          font-family: monospace;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .file-path.muted {
          color: var(--color-text-muted);
          font-style: italic;
        }
        .file-actions {
          display: flex;
          gap: 4px;
        }
        .icon-btn {
          background: none;
          border: none;
          font-size: 14px;
          cursor: pointer;
          padding: 4px 6px;
          border-radius: 4px;
          transition: background 0.15s;
        }
        .icon-btn:hover {
          background: var(--color-border);
        }
        .icon-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .action-buttons {
          display: flex;
          gap: 8px;
        }
        .modal-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.7);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }
        .modal-content {
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: 12px;
          padding: 24px;
          width: 90%;
          max-width: 400px;
        }
        .modal-content h4 {
          margin: 0 0 8px 0;
          font-size: 16px;
        }
        .modal-description {
          color: var(--color-text-muted);
          font-size: 13px;
          margin-bottom: 16px;
        }
        .email-input {
          width: 100%;
          padding: 10px 12px;
          background: var(--color-surface-alt);
          border: 1px solid var(--color-border);
          border-radius: 6px;
          color: var(--color-text);
          font-size: 14px;
          margin-bottom: 12px;
        }
        .email-input:focus {
          outline: none;
          border-color: var(--color-accent);
        }
        .error-message {
          color: var(--color-danger);
          font-size: 13px;
          margin-bottom: 12px;
        }
        .success-message {
          color: var(--color-success);
          font-size: 13px;
          margin-bottom: 12px;
        }
        .modal-actions {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
        }
      `}</style>
    </div>
  );
}
