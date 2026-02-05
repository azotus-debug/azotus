"use client";

import { useEffect, useState } from "react";
import { useOpsStore, QueueTrack } from "@/store/ops";
import { useNavigation } from "@/store/navigation";
import Badge from "@/components/common/Badge";
import Button from "@/components/common/Button";
import PageHeader from "@/components/layout/PageHeader";

// Stage display names and colors
const STAGE_CONFIG: Record<string, { label: string; color: string }> = {
  QUEUED: { label: "Queued", color: "default" },
  INGEST: { label: "Ingesting", color: "info" },
  TRANSCRIBED: { label: "Transcribed", color: "info" },
  TRANSLATING: { label: "Translating", color: "info" },
  CLOUD_TRANSLATING: { label: "Cloud Translating", color: "info" },
  AWAITING_REVIEW: { label: "Awaiting Review", color: "warning" },
  CLOUD_REVIEWING: { label: "Cloud Review", color: "warning" },
  REVIEWED: { label: "Reviewed", color: "success" },
  FINALIZING: { label: "Finalizing", color: "info" },
  BURNING: { label: "Burning", color: "info" },
  COMPLETE: { label: "Complete", color: "success" },
  COMPLETED: { label: "Completed", color: "success" },
  FAILED: { label: "Failed", color: "danger" },
  DEAD: { label: "Dead", color: "danger" },
};

// Format time remaining
const formatTimeRemaining = (hours: number | null): string => {
  if (hours === null) return "No deadline";
  if (hours < 0) {
    const overdue = Math.abs(hours);
    if (overdue < 24) return `${Math.round(overdue)}h overdue`;
    return `${Math.round(overdue / 24)}d overdue`;
  }
  if (hours < 1) return `${Math.round(hours * 60)}m left`;
  if (hours < 24) return `${Math.round(hours)}h left`;
  return `${Math.round(hours / 24)}d left`;
};

// Urgency badge
const UrgencyBadge = ({ urgency }: { urgency: string }) => {
  const variants: Record<string, "danger" | "warning" | "info" | "default"> = {
    overdue: "danger",
    urgent: "warning",
    soon: "info",
    normal: "default",
  };
  const labels: Record<string, string> = {
    overdue: "OVERDUE",
    urgent: "URGENT",
    soon: "DUE SOON",
    normal: "",
  };
  if (urgency === "normal") return null;
  return <Badge label={labels[urgency]} variant={variants[urgency]} />;
};

// Summary Cards Component
function SummaryCards() {
  const { summary, loadingSummary } = useOpsStore();

  if (loadingSummary || !summary) {
    return (
      <div className="ops-summary-grid">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="ops-summary-card loading">
            <div className="ops-summary-value">--</div>
            <div className="ops-summary-label">Loading...</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="ops-summary-grid">
      <div className="ops-summary-card">
        <div className="ops-summary-value">{summary.total_active}</div>
        <div className="ops-summary-label">Active Tracks</div>
      </div>
      <div className={`ops-summary-card ${summary.overdue > 0 ? "danger" : ""}`}>
        <div className="ops-summary-value">{summary.overdue}</div>
        <div className="ops-summary-label">Overdue</div>
      </div>
      <div className={`ops-summary-card ${summary.urgent > 0 ? "warning" : ""}`}>
        <div className="ops-summary-value">{summary.urgent}</div>
        <div className="ops-summary-label">Due Today</div>
      </div>
      <div className="ops-summary-card highlight">
        <div className="ops-summary-value">{summary.ready_for_delivery}</div>
        <div className="ops-summary-label">Ready to Ship</div>
      </div>
    </div>
  );
}

// Pipeline Funnel Component
function PipelineFunnel() {
  const { summary, fetchQueue, selectByStage } = useOpsStore();

  if (!summary) return null;

  const stages = Object.entries(summary.stage_counts)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);

  const maxCount = Math.max(...stages.map(([, c]) => c), 1);

  const handleStageClick = (stage: string) => {
    fetchQueue({ stage });
    selectByStage(stage);
  };

  return (
    <div className="ops-funnel">
      <h3 className="ops-section-title">Pipeline Status</h3>
      <div className="ops-funnel-bars">
        {stages.map(([stage, count]) => {
          const config = STAGE_CONFIG[stage] || { label: stage, color: "default" };
          const width = `${(count / maxCount) * 100}%`;
          const isBottleneck = summary.bottlenecks.some((b) => b.stage === stage);

          return (
            <button
              key={stage}
              className={`ops-funnel-bar ${isBottleneck ? "bottleneck" : ""}`}
              onClick={() => handleStageClick(stage)}
              type="button"
            >
              <div className="ops-funnel-bar-fill" style={{ width }} />
              <span className="ops-funnel-stage">{config.label}</span>
              <span className="ops-funnel-count">{count}</span>
              {isBottleneck && <span className="ops-bottleneck-badge">!</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Track Row Component
function TrackRow({ track, selected, onToggle, onSelect }: {
  track: QueueTrack;
  selected: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const stageConfig = STAGE_CONFIG[track.stage] || { label: track.stage, color: "default" };

  return (
    <div
      className={`ops-track-row ${selected ? "selected" : ""} urgency-${track.urgency}`}
      onClick={onSelect}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      role="button"
      tabIndex={0}
    >
      <div className="ops-track-checkbox">
        <input
          type="checkbox"
          checked={selected}
          onChange={(e) => {
            e.stopPropagation();
            onToggle();
          }}
        />
      </div>
      <div className="ops-track-main">
        <div className="ops-track-title">{track.program_title}</div>
        <div className="ops-track-meta">
          <span className="ops-track-lang">{track.language_code?.toUpperCase()}</span>
          {track.client && <span className="ops-track-client">{track.client}</span>}
        </div>
      </div>
      <div className="ops-track-status">
        <Badge
          label={stageConfig.label}
          variant={stageConfig.color as "default" | "info" | "success" | "warning" | "danger"}
        />
      </div>
      <div className="ops-track-deadline">
        <UrgencyBadge urgency={track.urgency} />
        <span className="ops-track-time">{formatTimeRemaining(track.hours_until_due)}</span>
      </div>
    </div>
  );
}

// Priority Queue Component
function PriorityQueue() {
  const { queue, selectedTrackIds, toggleTrackSelection, selectAllTracks, clearSelection, loadingQueue } = useOpsStore();
  const { selectProgram } = useNavigation();

  const allSelected = queue.length > 0 && selectedTrackIds.size === queue.length;

  return (
    <div className="ops-queue">
      <div className="ops-queue-header">
        <h3 className="ops-section-title">Priority Queue</h3>
        <div className="ops-queue-actions">
          <button
            className="ops-select-all"
            onClick={() => (allSelected ? clearSelection() : selectAllTracks())}
            type="button"
          >
            {allSelected ? "Deselect All" : "Select All"}
          </button>
          <span className="ops-selection-count">
            {selectedTrackIds.size > 0 && `${selectedTrackIds.size} selected`}
          </span>
        </div>
      </div>

      {loadingQueue ? (
        <div className="ops-loading">Loading queue...</div>
      ) : queue.length === 0 ? (
        <div className="ops-empty">No active tracks</div>
      ) : (
        <div className="ops-queue-list">
          {queue.map((track) => (
            <TrackRow
              key={track.id}
              track={track}
              selected={selectedTrackIds.has(track.id)}
              onToggle={() => toggleTrackSelection(track.id)}
              onSelect={() => selectProgram(track.program_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Batch Actions Bar Component
function BatchActionsBar() {
  const { selectedTrackIds, executeBatchAction, executingAction, lastActionResult, clearLastActionResult, queue } = useOpsStore();
  const [showConfirm, setShowConfirm] = useState<string | null>(null);

  const selectedCount = selectedTrackIds.size;
  if (selectedCount === 0) return null;

  // Calculate eligible counts for each action
  const eligibleCounts = {
    deliver: queue.filter((t) => selectedTrackIds.has(t.id) && ["COMPLETE", "COMPLETED"].includes(t.stage)).length,
    approve: queue.filter((t) => selectedTrackIds.has(t.id) && ["AWAITING_REVIEW", "CLOUD_REVIEWING"].includes(t.stage)).length,
    burn: queue.filter((t) => selectedTrackIds.has(t.id) && ["REVIEWED", "FINALIZING"].includes(t.stage)).length,
    retry: queue.filter((t) => selectedTrackIds.has(t.id) && ["FAILED", "DEAD"].includes(t.stage)).length,
  };

  const handleAction = async (action: string) => {
    setShowConfirm(null);
    await executeBatchAction(action);
  };

  return (
    <>
      <div className="ops-batch-bar">
        <span className="ops-batch-count">{selectedCount} selected</span>
        <div className="ops-batch-actions">
          {eligibleCounts.deliver > 0 && (
            <Button
              variant="primary"
              onClick={() => setShowConfirm("deliver")}
              disabled={executingAction}
            >
              Deliver ({eligibleCounts.deliver})
            </Button>
          )}
          {eligibleCounts.approve > 0 && (
            <Button
              variant="secondary"
              onClick={() => setShowConfirm("approve")}
              disabled={executingAction}
            >
              Approve ({eligibleCounts.approve})
            </Button>
          )}
          {eligibleCounts.burn > 0 && (
            <Button
              variant="secondary"
              onClick={() => setShowConfirm("burn")}
              disabled={executingAction}
            >
              Burn ({eligibleCounts.burn})
            </Button>
          )}
          {eligibleCounts.retry > 0 && (
            <Button
              variant="ghost"
              onClick={() => setShowConfirm("retry")}
              disabled={executingAction}
            >
              Retry ({eligibleCounts.retry})
            </Button>
          )}
        </div>
      </div>

      {/* Confirmation Modal */}
      {showConfirm && (
        <div className="ops-confirm-overlay" onClick={() => setShowConfirm(null)}>
          <div className="ops-confirm-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Confirm {showConfirm}</h3>
            <p>
              Execute <strong>{showConfirm}</strong> on{" "}
              <strong>{eligibleCounts[showConfirm as keyof typeof eligibleCounts]}</strong> eligible tracks?
            </p>
            {selectedCount > eligibleCounts[showConfirm as keyof typeof eligibleCounts] && (
              <p className="ops-confirm-note">
                Note: {selectedCount - eligibleCounts[showConfirm as keyof typeof eligibleCounts]} tracks are not eligible for this action and will be skipped.
              </p>
            )}
            <div className="ops-confirm-actions">
              <Button variant="ghost" onClick={() => setShowConfirm(null)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => handleAction(showConfirm)}>
                Confirm
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Action Result Toast */}
      {lastActionResult && (
        <div className="ops-result-toast" onClick={clearLastActionResult}>
          <div className="ops-result-content">
            <strong>{lastActionResult.action}</strong>: {lastActionResult.success} succeeded
            {lastActionResult.skipped > 0 && `, ${lastActionResult.skipped} skipped`}
            {lastActionResult.errors > 0 && `, ${lastActionResult.errors} failed`}
          </div>
          <button type="button" className="ops-result-close">×</button>
        </div>
      )}
    </>
  );
}

// Delivery Tracker Component
function DeliveryTracker() {
  const { pendingDeliveries, deliveryHistory, loadingDeliveries } = useOpsStore();

  return (
    <div className="ops-delivery-tracker">
      <h3 className="ops-section-title">Delivery Status</h3>

      {loadingDeliveries ? (
        <div className="ops-loading">Loading deliveries...</div>
      ) : (
        <>
          {/* Pending */}
          <div className="ops-delivery-section">
            <h4 className="ops-subsection-title">
              Pending ({pendingDeliveries.length})
            </h4>
            {pendingDeliveries.length === 0 ? (
              <div className="ops-empty-small">No pending deliveries</div>
            ) : (
              <div className="ops-delivery-list">
                {pendingDeliveries.slice(0, 5).map((d) => (
                  <div key={d.id} className="ops-delivery-item pending">
                    <span className="ops-delivery-title">{d.program_title}</span>
                    <span className="ops-delivery-lang">{d.language_code?.toUpperCase()}</span>
                    {d.client && <span className="ops-delivery-client">{d.client}</span>}
                  </div>
                ))}
                {pendingDeliveries.length > 5 && (
                  <div className="ops-delivery-more">
                    +{pendingDeliveries.length - 5} more
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Recent */}
          <div className="ops-delivery-section">
            <h4 className="ops-subsection-title">
              Recent Deliveries ({deliveryHistory.length})
            </h4>
            {deliveryHistory.length === 0 ? (
              <div className="ops-empty-small">No recent deliveries</div>
            ) : (
              <div className="ops-delivery-list">
                {deliveryHistory.slice(0, 5).map((d) => (
                  <div key={d.id} className="ops-delivery-item delivered">
                    <span className="ops-delivery-title">{d.program_title}</span>
                    <span className="ops-delivery-lang">{d.language_code?.toUpperCase()}</span>
                    <span className="ops-delivery-time">
                      {new Date(d.delivered_at).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Main Operations View
export default function OperationsView() {
  const { refreshAll, error } = useOpsStore();

  useEffect(() => {
    refreshAll();
    // Set up polling every 30 seconds
    const interval = setInterval(refreshAll, 30000);
    return () => clearInterval(interval);
  }, [refreshAll]);

  return (
    <section className="ops-view">
      <PageHeader
        title="Operations"
        subtitle="Priority queue and batch actions"
        actions={
          <Button variant="ghost" onClick={refreshAll}>
            Refresh
          </Button>
        }
      />

      {error && (
        <div className="ops-error">
          Error: {error}
        </div>
      )}

      <SummaryCards />

      <div className="ops-main-grid">
        <div className="ops-left-column">
          <PipelineFunnel />
          <PriorityQueue />
        </div>
        <div className="ops-right-column">
          <DeliveryTracker />
        </div>
      </div>

      <BatchActionsBar />
    </section>
  );
}
