"use client";

import { useState, useMemo, useCallback, useEffect } from "react";
import { useProgramsStore, Program } from "@/store/programs";
import { useToastStore } from "@/store/toast";
import { useNavigation } from "@/store/navigation";
import {
    Search, Film, Clock, CalendarDays, X,
    LayoutGrid, List, FolderOpen, ArrowUpDown,
    CheckSquare, Trash2, Check,
} from "lucide-react";
import { useProgramsQuery } from "@/hooks/useProgramsQuery";
import { API_BASE } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

type StatusFilter = "all" | "attention" | "active" | "completed";
type DateRange = "all" | "today" | "week" | "month";
type SortKey = "updated" | "created" | "title" | "client" | "duration";
type ViewMode = "grid" | "list";

export default function UnifiedProgramsView() {
    const { programs, deleteProgram } = useProgramsStore();
    const { addToast } = useToastStore();
    const { selectProgram } = useNavigation();

    const [searchQuery, setSearchQuery] = useState("");
    const [filter, setFilter] = useState<StatusFilter>("all");
    const [dateRange, setDateRange] = useState<DateRange>("all");
    const [sortBy, setSortBy] = useState<SortKey>("updated");
    const [groupByClient, setGroupByClient] = useState(false);
    const [viewMode, setViewMode] = useState<ViewMode>("grid");

    // ── Selection mode state ─────────────────────────────────────────
    const [selectMode, setSelectMode] = useState(false);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [deleteTarget, setDeleteTarget] = useState<{ ids: string[]; titles: string[] } | null>(null);
    const [deleting, setDeleting] = useState(false);

    const programsQuery = useProgramsQuery();
    const sourcePrograms = programsQuery.data ?? programs;

    // Escape key exits select mode
    useEffect(() => {
        const handleKey = (e: KeyboardEvent) => {
            if (e.key === "Escape" && selectMode && !deleteTarget) {
                setSelectMode(false);
                setSelected(new Set());
            }
        };
        window.addEventListener("keydown", handleKey);
        return () => window.removeEventListener("keydown", handleKey);
    }, [selectMode, deleteTarget]);

    // Clear selection when leaving select mode
    useEffect(() => {
        if (!selectMode) setSelected(new Set());
    }, [selectMode]);

    // ── Selection handlers ────────────────────────────────────────────

    const toggleSelect = useCallback((id: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setSelected((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const selectAllFiltered = useCallback(() => {
        setSelected(new Set(filteredPrograms.map((p) => p.id)));
    }, [/* filteredPrograms — defined below, referenced via closure */]);

    const deselectAll = useCallback(() => {
        setSelected(new Set());
    }, []);

    const requestDeleteSelected = useCallback(() => {
        const ids = Array.from(selected);
        const titles = ids.map((id) => sourcePrograms.find((p) => p.id === id)?.title || id);
        setDeleteTarget({ ids, titles });
    }, [selected, sourcePrograms]);

    const requestDeleteSingle = useCallback((program: Program, e: React.MouseEvent) => {
        e.stopPropagation();
        setDeleteTarget({ ids: [program.id], titles: [program.title] });
    }, []);

    const executeDelete = useCallback(async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        let deleted = 0;
        for (const id of deleteTarget.ids) {
            const ok = await deleteProgram(id);
            if (ok) {
                deleted++;
                if (deleteTarget.ids.length > 1) {
                    addToast(`Deleted ${deleted} of ${deleteTarget.ids.length} programs...`, "info");
                }
            } else {
                addToast(`Failed to delete program ${deleted + 1} of ${deleteTarget.ids.length}`, "error");
                break;
            }
        }
        if (deleted > 0) {
            addToast(
                deleted === 1
                    ? `Deleted "${deleteTarget.titles[0]}"`
                    : `Deleted ${deleted} programs`,
                "success"
            );
        }
        setDeleting(false);
        setDeleteTarget(null);
        setSelected(new Set());
        if (selectMode && deleted === deleteTarget.ids.length) {
            setSelectMode(false);
        }
    }, [deleteTarget, deleteProgram, addToast, selectMode]);

    // ── Filtering pipeline ──────────────────────────────────────────

    const filteredPrograms = useMemo(() => {
        let result = [...sourcePrograms];

        // Text search
        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            result = result.filter(
                (p) =>
                    p.title?.toLowerCase().includes(q) ||
                    p.client?.toLowerCase().includes(q) ||
                    p.original_filename?.toLowerCase().includes(q) ||
                    p.tracks?.some(t => t.language_name?.toLowerCase().includes(q))
            );
        }

        // Status filter
        if (filter === "attention") {
            result = result.filter((p) => p.needs_attention);
        } else if (filter === "active") {
            result = result.filter((p) => {
                if (!p.tracks || p.tracks.length === 0) return false;
                return p.tracks.some(t =>
                    !["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage) && t.stage !== "FAILED"
                );
            });
        } else if (filter === "completed") {
            result = result.filter((p) => {
                if (!p.tracks || p.tracks.length === 0) return false;
                return p.tracks.every(t => ["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage));
            });
        }

        // Date range filter
        if (dateRange !== "all") {
            const now = new Date();
            const cutoffs: Record<string, Date> = {
                today: new Date(now.getFullYear(), now.getMonth(), now.getDate()),
                week: new Date(now.getTime() - 7 * 86400000),
                month: new Date(now.getTime() - 30 * 86400000),
            };
            const cutoff = cutoffs[dateRange];
            result = result.filter((p) => new Date(p.updated_at) >= cutoff);
        }

        // Sort
        result.sort((a, b) => {
            let cmp = 0;
            switch (sortBy) {
                case "updated":
                    cmp = new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
                    break;
                case "created":
                    cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
                    break;
                case "title":
                    cmp = (a.title || "").localeCompare(b.title || "");
                    break;
                case "client":
                    cmp = (a.client || "zzz").localeCompare(b.client || "zzz");
                    break;
                case "duration":
                    cmp = (a.duration_seconds || 0) - (b.duration_seconds || 0);
                    break;
            }
            if (sortBy === "title" || sortBy === "client") return cmp;
            return -cmp;
        });

        return result;
    }, [sourcePrograms, searchQuery, filter, dateRange, sortBy]);

    // ── Group by client ─────────────────────────────────────────────

    const groupedPrograms = useMemo(() => {
        if (!groupByClient) return null;
        const groups: Record<string, Program[]> = {};
        filteredPrograms.forEach((p) => {
            const key = p.client && p.client.toLowerCase() !== "unknown" ? p.client : "Other";
            (groups[key] ??= []).push(p);
        });
        return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
    }, [filteredPrograms, groupByClient]);

    // ── Counts ──────────────────────────────────────────────────────

    const attentionCount = useMemo(() => sourcePrograms.filter((p) => p.needs_attention).length, [sourcePrograms]);
    const activeCount = useMemo(() =>
        sourcePrograms.filter((p) => p.tracks?.some(t =>
            !["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage) && t.stage !== "FAILED"
        )).length,
    [sourcePrograms]);
    const completedCount = useMemo(() =>
        sourcePrograms.filter((p) => p.tracks?.length && p.tracks.every(t =>
            ["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage)
        )).length,
    [sourcePrograms]);

    // ── Checkbox component ───────────────────────────────────────────

    const SelectBox = ({ id, e: _e }: { id: string; e?: React.MouseEvent }) => {
        const isChecked = selected.has(id);
        return (
            <div
                className={`select-checkbox ${isChecked ? "checked" : ""}`}
                onClick={(e) => toggleSelect(id, e)}
                role="checkbox"
                aria-checked={isChecked}
            >
                <Check size={12} color="#fff" strokeWidth={3} />
            </div>
        );
    };

    // ── Card renderer ───────────────────────────────────────────────

    const renderCard = (program: Program, i: number) => {
        const done = program.tracks?.filter(t => ["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage)).length || 0;
        const total = program.tracks?.length || 0;
        const isSelected = selected.has(program.id);

        return (
            <div key={program.id} className="program-card-wrapper">
                {selectMode && <SelectBox id={program.id} />}
                {!selectMode && (
                    <button
                        className="card-trash-btn"
                        onClick={(e) => requestDeleteSingle(program, e)}
                        title="Delete program"
                    >
                        <Trash2 size={14} />
                    </button>
                )}
                <motion.div
                    layout
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.96 }}
                    transition={{ duration: 0.2, delay: Math.min(i * 0.025, 0.12) }}
                    className={`program-card ${program.needs_attention ? "program-card--attention" : ""}`}
                    onClick={() => selectMode ? toggleSelect(program.id, { stopPropagation: () => {} } as React.MouseEvent) : selectProgram(program.id)}
                    style={{
                        animation: "none",
                        outline: isSelected ? "2px solid #6366f1" : "none",
                        outlineOffset: "-2px",
                    }}
                >
                    <div className="thumbnail-frame">
                        {program.thumbnail_path ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                                src={`${API_BASE}/api/v2/thumbnails/${program.id}`}
                                alt={program.title}
                                className="thumbnail"
                                onError={(e) => {
                                    e.currentTarget.style.display = "none";
                                    const parent = e.currentTarget.parentElement;
                                    if (parent) {
                                        parent.innerHTML =
                                            '<div class="thumbnail-placeholder"><svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="thumbnail-icon opacity-50"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/></svg></div>';
                                    }
                                }}
                            />
                        ) : (
                            <div className="thumbnail-placeholder">
                                <Film className="thumbnail-icon opacity-50" />
                            </div>
                        )}
                    </div>

                    <div className="program-card-header">
                        {program.client && program.client.toLowerCase() !== "unknown" && (
                            <div className="client-pill bg-white/5 border border-white/10 px-2 py-0.5 rounded-md text-[11px] font-medium text-gray-300">
                                {program.client}
                            </div>
                        )}
                        <div className="updated-time flex justify-end items-center gap-1 w-full text-[11px] text-muted font-medium">
                            <CalendarDays size={12} />
                            {new Date(program.updated_at).toLocaleDateString()}
                        </div>
                    </div>

                    <div className="card-title" title={program.title}>{program.title}</div>

                    <div className="card-meta-row mt-2">
                        <div className="flex items-center gap-1.5">
                            <Clock size={12} />
                            {program.duration_seconds ? `${Math.floor(program.duration_seconds / 60)}m` : "0m"}
                        </div>
                        <div>
                            {done} / {total} Tracks Completed
                        </div>
                    </div>

                    {program.tracks && program.tracks.length > 0 && (
                        <div className="track-badges mt-3 pt-3 border-t border-white/5">
                            {program.tracks.slice(0, 5).map((track) => {
                                const isComplete = ["COMPLETE", "DELIVERED", "FINALIZED"].includes(track.stage);
                                const isFailed = track.stage === "FAILED";
                                return (
                                    <div key={track.id} className={`track-dot ${isComplete ? "complete" : isFailed ? "failed" : "pending"}`}>
                                        {track.language_code.toUpperCase()}
                                    </div>
                                );
                            })}
                            {program.tracks.length > 5 && (
                                <div className="track-dot more">+{program.tracks.length - 5}</div>
                            )}
                        </div>
                    )}
                </motion.div>
            </div>
        );
    };

    // ── List row renderer ───────────────────────────────────────────

    const renderListRow = (program: Program, i: number) => {
        const isSelected = selected.has(program.id);
        return (
            <motion.div
                key={program.id}
                layout
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15, delay: Math.min(i * 0.02, 0.1) }}
                className="program-list-row"
                onClick={() => selectMode ? toggleSelect(program.id, { stopPropagation: () => {} } as React.MouseEvent) : selectProgram(program.id)}
                style={{
                    background: isSelected ? "rgba(99, 102, 241, 0.08)" : undefined,
                    gridTemplateColumns: selectMode
                        ? "32px 52px 1fr 64px 120px 100px"
                        : "52px 1fr 64px 120px 100px 32px",
                }}
            >
                {selectMode && (
                    <div className="list-select-cell">
                        <div
                            className={`select-checkbox ${isSelected ? "checked" : ""}`}
                            onClick={(e) => toggleSelect(program.id, e)}
                            style={{ position: "static" }}
                        >
                            <Check size={12} color="#fff" strokeWidth={3} />
                        </div>
                    </div>
                )}

                <div className="list-thumbnail">
                    {program.thumbnail_path ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={`${API_BASE}/api/v2/thumbnails/${program.id}`} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 4 }} />
                    ) : (
                        <Film size={14} className="opacity-40" />
                    )}
                </div>

                <div className="list-title-cell">
                    <span className="list-title">{program.title}</span>
                    {program.client && program.client.toLowerCase() !== "unknown" && (
                        <span className="list-client">{program.client}</span>
                    )}
                </div>

                <div className="text-xs text-muted tabular-nums">
                    {program.duration_seconds ? `${Math.floor(program.duration_seconds / 60)}m` : "\u2014"}
                </div>

                <div className="flex items-center gap-1.5">
                    {program.tracks?.slice(0, 4).map((track) => {
                        const isComplete = ["COMPLETE", "DELIVERED", "FINALIZED"].includes(track.stage);
                        const isFailed = track.stage === "FAILED";
                        return (
                            <div key={track.id} className={`track-dot ${isComplete ? "complete" : isFailed ? "failed" : "pending"}`} style={{ fontSize: 10, padding: "1px 5px" }}>
                                {track.language_code.toUpperCase()}
                            </div>
                        );
                    })}
                    {(program.tracks?.length || 0) > 4 && (
                        <span className="text-[10px] text-muted">+{(program.tracks?.length || 0) - 4}</span>
                    )}
                </div>

                <div className="text-[11px] text-muted text-right tabular-nums">
                    {new Date(program.updated_at).toLocaleDateString()}
                    {program.needs_attention && (
                        <span className="ml-2 inline-block w-1.5 h-1.5 rounded-full bg-red-400" />
                    )}
                </div>

                {!selectMode && (
                    <button
                        className="card-trash-btn"
                        onClick={(e) => requestDeleteSingle(program, e)}
                        title="Delete program"
                        style={{ position: "static", opacity: undefined }}
                    >
                        <Trash2 size={13} />
                    </button>
                )}
            </motion.div>
        );
    };

    // ── Grid or list block ──────────────────────────────────────────

    const renderPrograms = (progs: Program[]) =>
        viewMode === "grid" ? (
            <div className="program-grid">
                <AnimatePresence mode="popLayout">
                    {progs.map((p, i) => renderCard(p, i))}
                </AnimatePresence>
            </div>
        ) : (
            <div className="program-list">
                <AnimatePresence mode="popLayout">
                    {progs.map((p, i) => renderListRow(p, i))}
                </AnimatePresence>
            </div>
        );

    // ── Confirm dialog message ──────────────────────────────────────

    const confirmMessage = useMemo(() => {
        if (!deleteTarget) return "";
        const count = deleteTarget.ids.length;
        const maxShow = 5;
        const shown = deleteTarget.titles.slice(0, maxShow);
        const lines = shown.map((t) => `\u2022 ${t}`).join("\n");
        const extra = count > maxShow ? `\n\u2026 and ${count - maxShow} more` : "";
        return `${lines}${extra}\n\nThis will permanently remove all tracks, deliveries, and files from disk. This cannot be undone.`;
    }, [deleteTarget]);

    // ── Render ──────────────────────────────────────────────────────

    const isLoading = programsQuery.isPending && sourcePrograms.length === 0;
    const isError = programsQuery.isError && sourcePrograms.length === 0;
    const isEmpty = !isLoading && !isError && filteredPrograms.length === 0;
    const hasResults = !isLoading && !isError && filteredPrograms.length > 0;

    return (
        <div className="flex flex-col gap-5" style={{ animation: "fadeIn 0.3s ease" }}>
            {/* Header */}
            <div className="page-header">
                <div>
                    <h2 className="page-title">Program Library</h2>
                    <p className="page-subtitle">Your entire media portfolio</p>
                </div>
            </div>

            {/* Row 1: Filter tabs + search */}
            <div className="library-controls">
                <div className="filter-tabs">
                    <button className={`filter-tab ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
                        All <span className="tab-count">{sourcePrograms.length}</span>
                    </button>
                    <button className={`filter-tab ${filter === "attention" ? "active" : ""}`} onClick={() => setFilter("attention")}>
                        Needs Attention {attentionCount > 0 && <span className="tab-count">{attentionCount}</span>}
                    </button>
                    <button className={`filter-tab ${filter === "active" ? "active" : ""}`} onClick={() => setFilter("active")}>
                        Active {activeCount > 0 && <span className="tab-count">{activeCount}</span>}
                    </button>
                    <button className={`filter-tab ${filter === "completed" ? "active" : ""}`} onClick={() => setFilter("completed")}>
                        Completed {completedCount > 0 && <span className="tab-count">{completedCount}</span>}
                    </button>
                </div>

                <div className="library-search">
                    <Search size={14} className="text-muted" />
                    <input
                        type="text"
                        className="search-input"
                        placeholder="Search by title, client, language..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                    {searchQuery && (
                        <button className="search-clear" onClick={() => setSearchQuery("")}>
                            <X size={12} />
                        </button>
                    )}
                </div>
            </div>

            {/* Row 2: Toolbar */}
            <div className="library-toolbar">
                <div className="toolbar-group">
                    {(["all", "today", "week", "month"] as DateRange[]).map((dr) => (
                        <button
                            key={dr}
                            className={`toolbar-btn ${dateRange === dr ? "active" : ""}`}
                            onClick={() => setDateRange(dr)}
                        >
                            {dr === "all" ? "All Time" : dr === "today" ? "Today" : dr === "week" ? "This Week" : "This Month"}
                        </button>
                    ))}
                </div>

                <div className="toolbar-divider" />

                <div className="flex items-center gap-1.5">
                    <ArrowUpDown size={12} className="text-muted" />
                    <select
                        className="toolbar-select"
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value as SortKey)}
                    >
                        <option value="updated">Recently Updated</option>
                        <option value="created">Recently Added</option>
                        <option value="title">Title A{"\u2013"}Z</option>
                        <option value="client">Client</option>
                        <option value="duration">Duration</option>
                    </select>
                </div>

                <div className="toolbar-divider" />

                <button
                    className={`toolbar-btn ${groupByClient ? "active" : ""}`}
                    onClick={() => setGroupByClient(!groupByClient)}
                    title="Group by client"
                >
                    <FolderOpen size={13} />
                    Group
                </button>

                <button
                    className={`toolbar-btn ${selectMode ? "active" : ""}`}
                    onClick={() => setSelectMode(!selectMode)}
                    title={selectMode ? "Exit select mode (Esc)" : "Select programs to delete"}
                >
                    <CheckSquare size={13} />
                    Select
                </button>

                <div className="toolbar-group" style={{ marginLeft: "auto" }}>
                    <button
                        className={`toolbar-btn ${viewMode === "grid" ? "active" : ""}`}
                        onClick={() => setViewMode("grid")}
                        title="Grid view"
                    >
                        <LayoutGrid size={14} />
                    </button>
                    <button
                        className={`toolbar-btn ${viewMode === "list" ? "active" : ""}`}
                        onClick={() => setViewMode("list")}
                        title="List view"
                    >
                        <List size={14} />
                    </button>
                </div>
            </div>

            {/* Status line */}
            {hasResults && (
                <div className="library-status">
                    <span className="text-muted text-xs">
                        Showing {filteredPrograms.length} of {sourcePrograms.length} programs
                        {dateRange !== "all" && ` \u00b7 ${dateRange === "today" ? "Today" : dateRange === "week" ? "This week" : "This month"}`}
                        {groupByClient && " \u00b7 Grouped by client"}
                    </span>
                </div>
            )}

            {/* States */}
            {isLoading && (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <p>Loading programs...</p>
                </div>
            )}

            {isError && (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <p className="mb-3">Failed to load programs.</p>
                    <button className="search-clear" onClick={() => programsQuery.refetch()}>Retry</button>
                </div>
            )}

            {isEmpty && (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <Film size={32} className="mx-auto mb-4 opacity-50" />
                    <p>No programs found matching your filters.</p>
                </div>
            )}

            {/* Content */}
            {hasResults && !groupByClient && renderPrograms(filteredPrograms)}

            {hasResults && groupByClient && groupedPrograms?.map(([client, progs]) => (
                <div key={client} className="client-group">
                    <div className="client-group-header">
                        <span className="client-group-name">{client}</span>
                        <span className="client-group-count">{progs.length}</span>
                    </div>
                    {renderPrograms(progs)}
                </div>
            ))}

            {/* ─── Bulk Action Bar ─────────────────────────── */}
            <AnimatePresence>
                {selectMode && selected.size > 0 && (
                    <motion.div
                        className="bulk-action-bar"
                        initial={{ y: 80, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: 80, opacity: 0 }}
                        transition={{ duration: 0.2 }}
                    >
                        <span className="bulk-count">{selected.size} selected</span>

                        <button
                            className="bulk-btn bulk-btn--secondary"
                            onClick={() => {
                                if (selected.size === filteredPrograms.length) deselectAll();
                                else selectAllFiltered();
                            }}
                        >
                            {selected.size === filteredPrograms.length ? "Deselect All" : `Select All (${filteredPrograms.length})`}
                        </button>

                        <button
                            className="bulk-btn bulk-btn--danger"
                            onClick={requestDeleteSelected}
                            disabled={deleting}
                        >
                            <Trash2 size={13} style={{ marginRight: 4, display: "inline", verticalAlign: "-2px" }} />
                            Delete Selected
                        </button>

                        <button
                            className="bulk-btn bulk-btn--cancel"
                            onClick={() => { setSelectMode(false); setSelected(new Set()); }}
                        >
                            Cancel
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* ─── Confirm Dialog ──────────────────────────── */}
            <ConfirmDialog
                open={deleteTarget !== null}
                title={
                    deleteTarget?.ids.length === 1
                        ? `Delete "${deleteTarget.titles[0]}"?`
                        : `Delete ${deleteTarget?.ids.length} programs?`
                }
                message={confirmMessage}
                confirmLabel={deleting ? "Deleting..." : "Delete Permanently"}
                cancelLabel="Cancel"
                variant="danger"
                onConfirm={executeDelete}
                onCancel={() => !deleting && setDeleteTarget(null)}
            />
        </div>
    );
}
