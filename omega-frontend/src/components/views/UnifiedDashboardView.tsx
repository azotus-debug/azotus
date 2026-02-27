"use client";

import { useEffect, useMemo } from "react";
import { useProgramsStore } from "@/store/programs";
import { useNavigation } from "@/store/navigation";
import { Zap, ShieldAlert } from "lucide-react";
import { useProgramsQuery } from "@/hooks/useProgramsQuery";

export default function UnifiedDashboardView() {
    const { programs, activeTracks, pipelineStats, fetchActiveTracks, fetchPipelineStats } = useProgramsStore();
    const { selectProgram } = useNavigation();

    // Utilizing Phase 1: Real-Time WebSockets & React Query for caching
    useProgramsQuery();

    useEffect(() => {
        fetchActiveTracks();
        fetchPipelineStats();
    }, [fetchActiveTracks, fetchPipelineStats]);

    // Aggregate stats
    const actionItems = useMemo(() => {
        return activeTracks.filter(t =>
            t.stage === "FAILED" ||
            t.stage === "DEAD" ||
            t.stage.includes("ERROR") ||
            t.stage === "AWAITING_REVIEW" ||
            t.stage === "AWAITING_APPROVAL"
        );
    }, [activeTracks]);

    const activeProcessing = useMemo(() => {
        return activeTracks.filter(t =>
            !actionItems.includes(t) &&
            !(["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage))
        );
    }, [activeTracks, actionItems]);

    return (
        <div className="flex flex-col gap-6" style={{ animation: "fadeIn 0.3s ease" }}>
            <div className="page-header">
                <div>
                    <h2 className="page-title">Flight Deck</h2>
                    <p className="page-subtitle">Real-time production overview</p>
                </div>

                {pipelineStats && (
                    <div className="pipeline-summary flex items-center gap-6">
                        <div className="summary-item">
                            <span className="summary-label">Active Tracks</span>
                            <span className="summary-value">{pipelineStats.total_active}</span>
                        </div>
                        <div className="summary-divider" />
                        <div className="summary-item">
                            <span className="summary-label">Needs Attention</span>
                            <span className={`summary-value ${pipelineStats.needs_attention > 0 ? 'summary-value--alert' : ''}`}>
                                {pipelineStats.needs_attention}
                            </span>
                        </div>
                        <div className="summary-divider" />
                        <div className="summary-item">
                            <span className="summary-label">Blocked</span>
                            <span className={`summary-value ${pipelineStats.blocked > 0 ? 'summary-value--alert' : ''}`}>
                                {pipelineStats.blocked}
                            </span>
                        </div>
                    </div>
                )}
            </div>

            <div className="stage-groups">
                {/* Action Required Queue */}
                {actionItems.length > 0 && (
                    <div className="stage-group" style={{ borderColor: 'rgba(239, 68, 68, 0.3)', boxShadow: '0 0 20px rgba(239, 68, 68, 0.05)' }}>
                        <div className="stage-header" style={{ background: 'rgba(239, 68, 68, 0.05)' }}>
                            <h3 className="stage-title flex items-center gap-2 text-red-400">
                                <ShieldAlert size={16} />
                                Action Required ({actionItems.length})
                            </h3>
                        </div>
                        <div className="stage-tracks">
                            {actionItems.map(track => {
                                const program = programs.find(p => p.id === track.program_id);
                                return (
                                    <div key={track.id} className="pipeline-track-row" onClick={() => selectProgram(track.program_id)}>
                                        <div className="track-info">
                                            <div className={`status-indicator indicator--blocked`} />
                                            <div className="track-text">
                                                <span className="track-program">{program?.title || track.program_id}</span>
                                                <span className="track-language flex items-center gap-1">
                                                    {track.language_name} • <span style={{ color: '#f87171' }}>{track.stage.replace(/_/g, " ")}</span>
                                                </span>
                                            </div>
                                        </div>
                                        <div className="track-progress">
                                            <span className="text-muted text-xs truncate max-w-[300px]">{track.status || "Check logs"}</span>
                                        </div>
                                        <div className="track-status-badge flex items-center justify-end">
                                            <div className="flex items-center gap-2 text-xs font-medium text-red-400 bg-red-500/10 px-2 py-1 rounded-md border border-red-500/20">
                                                <div className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
                                                Needs Action
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Active Processing Queue */}
                <div className="stage-group">
                    <div className="stage-header">
                        <h3 className="stage-title flex items-center gap-2">
                            <Zap size={16} style={{ color: '#60a5fa' }} />
                            Active Processing ({activeProcessing.length})
                        </h3>
                    </div>
                    <div className="stage-tracks">
                        {activeProcessing.length === 0 ? (
                            <div className="qc-empty">No tracks are currently passing through the pipeline.</div>
                        ) : (
                            activeProcessing.map(track => {
                                const program = programs.find(p => p.id === track.program_id);
                                return (
                                    <div key={track.id} className="pipeline-track-row" onClick={() => selectProgram(track.program_id)}>
                                        <div className="track-info">
                                            <div className="status-indicator indicator--active" />
                                            <div className="track-text">
                                                <span className="track-program">{program?.title || track.program_id}</span>
                                                <span className="track-language">{track.language_name} • {track.type.toUpperCase()}</span>
                                            </div>
                                        </div>

                                        <div className="track-progress flex flex-col gap-1 w-full" style={{ alignItems: 'flex-start', paddingTop: '4px', paddingBottom: '4px' }}>
                                            <div className="flex justify-between text-xs text-muted mb-1 w-full">
                                                <span>{track.stage}</span>
                                                <span className="tabular-nums font-mono">{track.progress.toFixed(0)}%</span>
                                            </div>
                                            <div style={{ height: '4px', width: '100%', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', overflow: 'hidden' }}>
                                                <div style={{ height: '100%', background: '#3b82f6', width: `${Math.max(2, track.progress)}%`, transition: 'width 0.5s ease' }} />
                                            </div>
                                            <span style={{ fontSize: '10px' }} className="text-muted truncate mt-1">{track.status}</span>
                                        </div>

                                        <div className="track-status-badge flex items-center justify-end">
                                            <div className="flex items-center gap-2 text-xs font-medium text-blue-400">
                                                <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                                                Processing
                                            </div>
                                        </div>
                                    </div>
                                )
                            })
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
