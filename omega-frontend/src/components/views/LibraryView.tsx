"use client";

import { CSSProperties, useEffect, useMemo, useState } from "react";
import { Clock, Trash2 } from "lucide-react";
import Badge from "@/components/common/Badge";
import PageHeader from "@/components/layout/PageHeader";
import { ProgramGridSkeleton } from "@/components/common/Skeleton";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore, Program } from "@/store/programs";
import { useToastStore } from "@/store/toast";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

type LibraryFilter = "all" | "in_progress" | "complete" | "delivered";

const FILTER_TABS: { key: LibraryFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "in_progress", label: "In Progress" },
  { key: "complete", label: "Complete" },
  { key: "delivered", label: "Delivered" },
];

const getFilterStatus = (program: Program): Exclude<LibraryFilter, "all"> => {
  const tracks = program.tracks || [];
  if (tracks.length === 0) return "in_progress";

  const allDelivered = tracks.every((t) => t.stage === "DELIVERED");
  if (allDelivered) return "delivered";

  const allDone = tracks.every((t) => t.stage === "COMPLETE" || t.stage === "DELIVERED");
  if (allDone) return "complete";

  return "in_progress";
};

const hasFailedTracks = (program: Program): boolean =>
  (program.tracks || []).some((track) => track.stage === "FAILED");

const getStatusVariant = (program: Program): "success" | "warning" | "info" | "error" => {
  if (program.needs_attention) return "warning";

  const tracks = program.tracks || [];
  if (tracks.length === 0) return "info";

  const allDelivered = tracks.every((t) => t.stage === "DELIVERED");
  if (allDelivered) return "success";

  const allComplete = tracks.every((t) => t.stage === "COMPLETE" || t.stage === "DELIVERED");
  if (allComplete) return "success";

  return "info";
};

const getStatusText = (program: Program): string => {
  if (program.needs_attention) return "Needs Attention";

  const tracks = program.tracks || [];
  if (tracks.length === 0) return "No Tracks";

  const filterStatus = getFilterStatus(program);
  if (filterStatus === "delivered") return "Delivered";
  if (filterStatus === "complete") return "Complete";
  return "In Progress";
};

const getStyleVariant = (style?: string): "success" | "warning" | "info" | "error" => {
  const normalized = (style || "").toLowerCase();
  if (normalized.includes("tv") || normalized.includes("broadcast")) return "warning";
  if (normalized.includes("modern")) return "success";
  if (normalized.includes("classic")) return "info";
  return "info";
};

const formatDuration = (seconds?: number): string => {
  if (!seconds) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};

const formatRelativeTime = (iso?: string): string => {
  if (!iso) return "—";
  const parsed = new Date(iso);
  const timestamp = parsed.getTime();
  if (Number.isNaN(timestamp)) return "—";
  const diffMs = Date.now() - timestamp;
  const diffMinutes = Math.floor(diffMs / 60000);
  if (diffMinutes < 1) return "Just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  const diffWeeks = Math.floor(diffDays / 7);
  if (diffWeeks < 5) return `${diffWeeks}w ago`;
  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) return `${diffMonths}mo ago`;
  const diffYears = Math.floor(diffDays / 365);
  return `${diffYears}y ago`;
};

const hashString = (value: string): number => {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = value.charCodeAt(i) + ((hash << 5) - hash);
  }
  return hash;
};

const clientStyleFromName = (value: string): CSSProperties => {
  const hue = Math.abs(hashString(value)) % 360;
  return { "--client-hue": `${hue}` } as CSSProperties;
};

const sortPrograms = (items: Program[]): Program[] => {
  return [...items].sort((a, b) => {
    if (a.needs_attention !== b.needs_attention) {
      return a.needs_attention ? -1 : 1;
    }
    const aTime = new Date(a.updated_at).getTime();
    const bTime = new Date(b.updated_at).getTime();
    if (Number.isNaN(aTime) || Number.isNaN(bTime)) return 0;
    return bTime - aTime;
  });
};

export default function LibraryView() {
  const { selectProgram } = useNavigation();
  const { programs, loading, error, fetchPrograms } = useProgramsStore();
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [search, setSearch] = useState("");
  const addToast = useToastStore(s => s.addToast);
  const [confirmDelete, setConfirmDelete] = useState<{id: string, title: string} | null>(null);

  useEffect(() => {
    fetchPrograms();
  }, [fetchPrograms]);

  const handleDeleteProgram = (programId: string, title: string, event: React.MouseEvent) => {
    event.stopPropagation();
    setConfirmDelete({ id: programId, title });
  };

  const executeDelete = async () => {
    if (!confirmDelete) return;
    const { id, title } = confirmDelete;
    setConfirmDelete(null);
    try {
      const response = await fetch(`${API_BASE}/api/v2/programs/${id}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
      });
      const data = await response.json();
      if (data.success) {
        addToast(`"${title}" deleted`, "success");
        fetchPrograms();
      } else {
        addToast(`Delete failed: ${data.error || "Unknown error"}`, "error");
      }
    } catch (err) {
      addToast(`Delete failed: ${err}`, "error");
    }
  };

  const sortedPrograms = useMemo(() => sortPrograms(programs), [programs]);

  // Get the 5 most recently updated programs for quick access
  const recentPrograms = useMemo(() => {
    return [...programs]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5);
  }, [programs]);

  const filterCounts = useMemo(() => {
    const counts = {
      all: programs.length,
      in_progress: 0,
      complete: 0,
      delivered: 0,
    };
    programs.forEach((program) => {
      const status = getFilterStatus(program);
      counts[status] += 1;
    });
    return counts;
  }, [programs]);

  const filteredPrograms = useMemo(() => {
    const query = search.trim().toLowerCase();
    return sortedPrograms.filter((program) => {
      if (filter !== "all" && getFilterStatus(program) !== filter) return false;
      if (!query) return true;

      const title = (program.title || "").toLowerCase();
      const client = (program.client || "").toLowerCase();
      return title.includes(query) || client.includes(query);
    });
  }, [sortedPrograms, filter, search]);

  if (loading && programs.length === 0) {
    return (
      <section className="library-view">
        <PageHeader title="Library" subtitle="Loading programs..." />
        <ProgramGridSkeleton count={8} />
      </section>
    );
  }

  if (error) {
    return (
      <section className="library-view">
        <PageHeader title="Library" subtitle="Error loading programs" />
        <div className="error-message">{error}</div>
      </section>
    );
  }

  return (
    <section className="library-view">
      <PageHeader title="Library" subtitle={`${programs.length} programs`} />
      <div className="library-controls">
        <div className="filter-tabs" role="tablist" aria-label="Program filters">
          {FILTER_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`filter-tab${filter === tab.key ? " active" : ""}`}
              onClick={() => setFilter(tab.key)}
              aria-pressed={filter === tab.key}
            >
              {tab.label}
              <span className="tab-count">
                {tab.key === "all" ? filterCounts.all : filterCounts[tab.key]}
              </span>
            </button>
          ))}
        </div>

        <div className="library-search">
          <input
            type="text"
            className="search-input"
            placeholder="Search programs..."
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search programs"
          />
          {search.trim().length > 0 && (
            <button type="button" className="search-clear" onClick={() => setSearch("")}>
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Recently Updated Section */}
      {recentPrograms.length > 0 && filter === "all" && !search.trim() && (
        <div className="recent-section">
          <div className="recent-header">
            <Clock size={16} />
            <span>Recently Updated</span>
          </div>
          <div className="recent-row">
            {recentPrograms.map((program) => (
              <div
                key={program.id}
                className={`recent-card${program.needs_attention ? " recent-card--attention" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => selectProgram(program.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") selectProgram(program.id);
                }}
              >
                <button
                  className="recent-delete-btn"
                  title="Delete program"
                  onClick={(e) => handleDeleteProgram(program.id, program.title, e)}
                >
                  <Trash2 size={12} />
                </button>
                <div className="recent-thumb">
                  {program.thumbnail_path ? (
                    <img
                      src={`${API_BASE}/api/v2/thumbnails/${program.id}`}
                      alt={program.title}
                      onError={(event) => {
                        (event.target as HTMLImageElement).style.display = "none";
                      }}
                    />
                  ) : (
                    <span className="recent-thumb-icon">🎬</span>
                  )}
                </div>
                <div className="recent-info">
                  <span className="recent-title">{program.title}</span>
                  <span className="recent-meta">
                    {formatRelativeTime(program.updated_at)} · {program.track_completion}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {filteredPrograms.length === 0 ? (
        <div className="empty-state">
          <span style={{ fontSize: "40px" }}>🔎</span>
          <p>No programs match the current filter.</p>
        </div>
      ) : (
        <div className="program-grid">
          {filteredPrograms.map((program) => {
            const clientLabel =
              program.client && program.client !== "unknown" ? program.client : "";
            const updatedLabel = formatRelativeTime(program.updated_at);
            const failedBadge = hasFailedTracks(program);
            const cardClass = `program-card${program.needs_attention ? " program-card--attention" : ""}`;

            return (
              <div
                key={program.id}
                className={cardClass}
                role="button"
                tabIndex={0}
                onClick={() => selectProgram(program.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") selectProgram(program.id);
                }}
              >
                <div className="program-card-header">
                  <Badge
                    label={program.default_style || "Classic"}
                    variant={getStyleVariant(program.default_style)}
                  />
                  <div className="flex items-center gap-2">
                    <span className="updated-time">Updated {updatedLabel}</span>
                    <button
                      className="delete-btn"
                      title="Delete program"
                      onClick={(e) => handleDeleteProgram(program.id, program.title, e)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                <div className="thumbnail-frame">
                  {program.thumbnail_path ? (
                    <img
                      src={`${API_BASE}/api/v2/thumbnails/${program.id}`}
                      alt={program.title}
                      className="thumbnail"
                      onError={(event) => {
                        (event.target as HTMLImageElement).style.display = "none";
                      }}
                    />
                  ) : (
                    <div className="thumbnail-placeholder">
                      <span className="thumbnail-icon">🎬</span>
                    </div>
                  )}
                </div>

                <div className="card-title">{program.title}</div>

                <div className="card-meta-row">
                  <span>{program.track_completion} tracks</span>
                  <span>{formatDuration(program.duration_seconds)}</span>
                </div>

                <div className="card-meta-row">
                  {clientLabel && (
                    <span className="client-pill" style={clientStyleFromName(clientLabel)}>
                      <span className="client-dot" />
                      {clientLabel}
                    </span>
                  )}
                </div>

                {program.tracks && program.tracks.length > 0 && (
                  <div className="track-badges">
                    {program.tracks.slice(0, 4).map((track) => (
                      <span
                        key={track.id}
                        className={`track-dot ${track.stage === "COMPLETE" || track.stage === "DELIVERED"
                          ? "complete"
                          : track.stage === "FAILED"
                            ? "failed"
                            : "pending"
                          }`}
                        title={`${track.language_name} ${track.type} - ${track.stage}`}
                      >
                        {track.language_code.toUpperCase()}
                      </span>
                    ))}
                    {program.tracks.length > 4 && (
                      <span className="track-dot more">+{program.tracks.length - 4}</span>
                    )}
                  </div>
                )}

                <div className="program-footer">
                  <Badge label={getStatusText(program)} variant={getStatusVariant(program)} />
                  {failedBadge && !program.needs_attention && (
                    <Badge label="Failed" variant="error" />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete Program"
        message={`Delete "${confirmDelete?.title}" and all associated files? This cannot be undone.`}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        variant="danger"
        onConfirm={executeDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </section>
  );
}
