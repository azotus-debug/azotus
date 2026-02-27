"use client";

import { useState, useMemo } from "react";
import { useProgramsStore } from "@/store/programs";
import { useNavigation } from "@/store/navigation";
import { Search, Film, Clock, CalendarDays, X } from "lucide-react";
import { useProgramsQuery } from "@/hooks/useProgramsQuery";

export default function UnifiedProgramsView() {
    const { programs } = useProgramsStore();
    const { selectProgram } = useNavigation();
    const [searchQuery, setSearchQuery] = useState("");
    const [filter, setFilter] = useState<"all" | "attention" | "active" | "recent">("all");

    // Phase 1: Real-Time WebSockets & React Query for caching
    const programsQuery = useProgramsQuery();
    const sourcePrograms = programsQuery.data ?? programs;

    // Filtering
    const filteredPrograms = useMemo(() => {
        let result = [...sourcePrograms];

        if (searchQuery) {
            const q = searchQuery.toLowerCase();
            result = result.filter(
                (p) =>
                    p.title?.toLowerCase().includes(q) ||
                    p.client?.toLowerCase().includes(q) ||
                    p.original_filename?.toLowerCase().includes(q)
            );
        }

        if (filter === "attention") {
            result = result.filter((p) => p.needs_attention);
        } else if (filter === "active") {
            result = result.filter((p) => {
                if (!p.tracks || p.tracks.length === 0) return false;
                return p.tracks.some(t => !["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage) && t.stage !== "FAILED");
            });
        }

        // Sort by updated_at descending
        result.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());

        return result;
    }, [sourcePrograms, searchQuery, filter]);

    return (
        <div className="flex flex-col gap-6" style={{ animation: "fadeIn 0.3s ease" }}>
            <div className="page-header">
                <div>
                    <h2 className="page-title">Program Library</h2>
                    <p className="page-subtitle">Your entire media portfolio</p>
                </div>
            </div>

            <div className="library-controls">
                <div className="filter-tabs">
                    <button className={`filter-tab ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>
                        All <span className="tab-count">{sourcePrograms.length}</span>
                    </button>
                    <button className={`filter-tab ${filter === "attention" ? "active" : ""}`} onClick={() => setFilter("attention")}>
                        Needs Attention <span className="tab-count">{sourcePrograms.filter(p => p.needs_attention).length}</span>
                    </button>
                    <button className={`filter-tab ${filter === "active" ? "active" : ""}`} onClick={() => setFilter("active")}>
                        Active
                    </button>
                </div>

                <div className="library-search">
                    <Search size={14} className="text-muted" />
                    <input
                        type="text"
                        className="search-input"
                        placeholder="Search by title, client..."
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

            {programsQuery.isPending && sourcePrograms.length === 0 && (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <p>Loading programs...</p>
                </div>
            )}

            {programsQuery.isError && sourcePrograms.length === 0 && (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: "rgba(255,255,255,0.02)" }}>
                    <p className="mb-3">
                        Failed to load programs.
                    </p>
                    <button className="search-clear" onClick={() => programsQuery.refetch()}>
                        Retry
                    </button>
                </div>
            )}

            {!programsQuery.isPending && !programsQuery.isError && filteredPrograms.length === 0 ? (
                <div className="p-12 text-center text-muted border border-white/10 rounded-xl" style={{ background: 'rgba(255,255,255,0.02)' }}>
                    <Film size={32} className="mx-auto mb-4 opacity-50" />
                    <p>No programs found matching your filters.</p>
                </div>
            ) : !programsQuery.isPending && !programsQuery.isError ? (
                <div className="program-grid">
                    {filteredPrograms.map((program) => {
                        const completedCount = program.tracks?.filter(t => ["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage)).length || 0;
                        const totalCount = program.tracks?.length || 0;

                        return (
                            <div
                                key={program.id}
                                className={`program-card ${program.needs_attention ? 'program-card--attention' : ''}`}
                                onClick={() => selectProgram(program.id)}
                            >
                                <div className="thumbnail-frame">
                                    {program.thumbnail_path ? (
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img
                                            src={program.thumbnail_path}
                                            alt={program.title}
                                            className="thumbnail"
                                            onError={(e) => {
                                                e.currentTarget.style.display = 'none';
                                                const parent = e.currentTarget.parentElement;
                                                if (parent) {
                                                    parent.innerHTML = '<div class="thumbnail-placeholder"><svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="thumbnail-icon opacity-50"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/></svg></div>';
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
                                        {program.duration_seconds ? `${Math.floor(program.duration_seconds / 60)}m` : '0m'}
                                    </div>
                                    <div>
                                        {completedCount} / {totalCount} Tracks Completed
                                    </div>
                                </div>

                                {program.tracks && program.tracks.length > 0 && (
                                    <div className="track-badges mt-3 pt-3 border-t border-white/5">
                                        {program.tracks.slice(0, 5).map(track => {
                                            const isComplete = ["COMPLETE", "DELIVERED", "FINALIZED"].includes(track.stage);
                                            const isFailed = track.stage === "FAILED";
                                            return (
                                                <div key={track.id} className={`track-dot ${isComplete ? 'complete' : isFailed ? 'failed' : 'pending'}`}>
                                                    {track.language_code.toUpperCase()}
                                                </div>
                                            );
                                        })}
                                        {program.tracks.length > 5 && (
                                            <div className="track-dot more">+{program.tracks.length - 5}</div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            ) : null}
        </div>
    );
}
