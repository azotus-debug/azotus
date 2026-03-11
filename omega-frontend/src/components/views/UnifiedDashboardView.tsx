"use client";

import { useEffect, useMemo } from "react";
import { useProgramsStore } from "@/store/programs";
import { useNavigation } from "@/store/navigation";
import {
    Film, Zap, ShieldAlert, CheckCircle, TrendingUp,
} from "lucide-react";
import { useProgramsQuery } from "@/hooks/useProgramsQuery";
import { motion, AnimatePresence } from "framer-motion";

/* ──────────────────────────────────────────────────────────── */
/*  Circular progress ring — inline SVG, no extra dependency   */
/* ──────────────────────────────────────────────────────────── */
function CircularProgress({ value, size = 44, stroke = 4 }: { value: number; size?: number; stroke?: number }) {
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ - (Math.min(value, 100) / 100) * circ;

    return (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: "rotate(-90deg)" }}>
            <circle cx={size / 2} cy={size / 2} r={r}
                fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke} />
            <circle cx={size / 2} cy={size / 2} r={r}
                fill="none" stroke="#6366f1" strokeWidth={stroke}
                strokeDasharray={circ} strokeDashoffset={offset}
                strokeLinecap="round"
                style={{ transition: "stroke-dashoffset 0.6s ease" }} />
            <text x={size / 2} y={size / 2} textAnchor="middle" dominantBaseline="central"
                fill="var(--text-primary)" fontSize="11" fontWeight="700"
                style={{ transform: "rotate(90deg)", transformOrigin: "center" }}>
                {Math.round(value)}%
            </text>
        </svg>
    );
}

/* ──────────────────────────────────────────────────────────── */
/*  Helpers                                                    */
/* ──────────────────────────────────────────────────────────── */
function getGreeting(): string {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 18) return "Good afternoon";
    return "Good evening";
}

function formatRelativeDate(iso: string): string {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    const diffD = Math.floor(diffH / 24);
    if (diffD === 1) return "Yesterday";
    if (diffD < 7) return `${diffD}d ago`;
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* ──────────────────────────────────────────────────────────── */
/*  Motion variants                                            */
/* ──────────────────────────────────────────────────────────── */
const cardVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: (i: number) => ({
        opacity: 1, y: 0,
        transition: { delay: i * 0.06, duration: 0.35, ease: "easeOut" as const },
    }),
};

const rowVariants = {
    hidden: { opacity: 0, x: -8 },
    visible: { opacity: 1, x: 0, transition: { duration: 0.25 } },
    exit: { opacity: 0, x: 8, transition: { duration: 0.15 } },
};

/* ──────────────────────────────────────────────────────────── */
/*  Component                                                  */
/* ──────────────────────────────────────────────────────────── */
export default function UnifiedDashboardView() {
    const {
        programs, activeTracks, deliveries, pipelineStats,
        fetchActiveTracks, fetchPipelineStats, fetchDeliveries,
    } = useProgramsStore();
    const { selectProgram } = useNavigation();

    useProgramsQuery();

    useEffect(() => {
        fetchActiveTracks();
        fetchPipelineStats();
        fetchDeliveries(7);
    }, [fetchActiveTracks, fetchPipelineStats, fetchDeliveries]);

    /* ── Derived data ────────────────────────────────────── */
    const actionItems = useMemo(() =>
        activeTracks.filter(t =>
            t.stage === "FAILED" || t.stage === "DEAD" ||
            t.stage.includes("ERROR") ||
            t.stage === "AWAITING_REVIEW" || t.stage === "AWAITING_APPROVAL"
        ), [activeTracks]);

    const activeProcessing = useMemo(() =>
        activeTracks.filter(t =>
            !actionItems.includes(t) &&
            !["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage)
        ), [activeTracks, actionItems]);

    const recentDeliveries = useMemo(() =>
        [...deliveries]
            .sort((a, b) => new Date(b.delivered_at).getTime() - new Date(a.delivered_at).getTime())
            .slice(0, 8),
        [deliveries]);

    /* ── Stats ───────────────────────────────────────────── */
    const totalPrograms = programs.length;

    const pipelineCompletion = useMemo(() => {
        if (!programs.length) return 0;
        const allTracks = programs.flatMap(p => p.tracks);
        if (!allTracks.length) return 0;
        const done = allTracks.filter(t =>
            ["COMPLETE", "DELIVERED", "FINALIZED"].includes(t.stage)
        ).length;
        return Math.round((done / allTracks.length) * 100);
    }, [programs]);

    const updatedToday = useMemo(() => {
        const todayStart = new Date();
        todayStart.setHours(0, 0, 0, 0);
        return programs.filter(p => new Date(p.updated_at) >= todayStart).length;
    }, [programs]);

    const greetingSub = useMemo(() => {
        if (actionItems.length > 0) return `${actionItems.length} item${actionItems.length > 1 ? "s" : ""} need${actionItems.length === 1 ? "s" : ""} your attention`;
        if (updatedToday > 0) return `${updatedToday} program${updatedToday > 1 ? "s" : ""} updated today`;
        return "All quiet on the production front";
    }, [actionItems, updatedToday]);

    /* ── Stat card definitions ───────────────────────────── */
    const statCards = [
        {
            label: "Total Programs",
            value: totalPrograms,
            icon: <Film size={18} />,
            iconBg: "rgba(99, 102, 241, 0.12)",
            iconColor: "#818cf8",
        },
        {
            label: "Pipeline Complete",
            value: null, // uses CircularProgress instead
            custom: <CircularProgress value={pipelineCompletion} />,
            icon: null,
            iconBg: "",
            iconColor: "",
        },
        {
            label: "Active Tracks",
            value: pipelineStats?.total_active ?? activeProcessing.length,
            icon: <Zap size={18} />,
            iconBg: "rgba(59, 130, 246, 0.12)",
            iconColor: "#60a5fa",
        },
        {
            label: "Delivered This Week",
            value: deliveries.length,
            icon: <CheckCircle size={18} />,
            iconBg: "rgba(34, 197, 94, 0.12)",
            iconColor: "#4ade80",
        },
        {
            label: "Needs Attention",
            value: pipelineStats?.needs_attention ?? actionItems.length,
            icon: <ShieldAlert size={18} />,
            iconBg: (pipelineStats?.needs_attention ?? actionItems.length) > 0
                ? "rgba(239, 68, 68, 0.12)" : "rgba(255,255,255,0.04)",
            iconColor: (pipelineStats?.needs_attention ?? actionItems.length) > 0
                ? "#f87171" : "var(--text-muted)",
        },
    ];

    /* ── Render ───────────────────────────────────────────── */
    return (
        <div className="flex flex-col gap-6" style={{ animation: "fadeIn 0.3s ease" }}>

            {/* ─── GREETING ──────────────────────────────── */}
            <div className="page-header">
                <div>
                    <h2 className="page-title">{getGreeting()}</h2>
                    <p className="page-subtitle">{greetingSub}</p>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted">
                    <TrendingUp size={14} style={{ color: "#6366f1" }} />
                    Flight Deck
                </div>
            </div>

            {/* ─── STAT CARDS ─────────────────────────────── */}
            <div className="dashboard-stats">
                {statCards.map((card, i) => (
                    <motion.div
                        key={card.label}
                        className="stat-card"
                        variants={cardVariants}
                        initial="hidden"
                        animate="visible"
                        custom={i}
                    >
                        {card.custom ? (
                            /* Circular progress card */
                            <>
                                <div className="stat-card-content">
                                    <span className="stat-card-label">{card.label}</span>
                                </div>
                                {card.custom}
                            </>
                        ) : (
                            /* Normal stat card */
                            <>
                                <div className="stat-card-content">
                                    <span className="stat-card-value">{card.value}</span>
                                    <span className="stat-card-label">{card.label}</span>
                                </div>
                                {card.icon && (
                                    <div className="stat-card-icon"
                                        style={{ background: card.iconBg, color: card.iconColor }}>
                                        {card.icon}
                                    </div>
                                )}
                            </>
                        )}
                    </motion.div>
                ))}
            </div>

            <div className="stage-groups">

                {/* ─── ACTION REQUIRED ───────────────────── */}
                <AnimatePresence>
                    {actionItems.length > 0 && (
                        <motion.div
                            className="stage-group"
                            style={{ borderColor: "rgba(239, 68, 68, 0.3)", boxShadow: "0 0 20px rgba(239, 68, 68, 0.05)" }}
                            initial={{ opacity: 0, y: 12 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, height: 0, marginBottom: 0 }}
                        >
                            <div className="stage-header" style={{ background: "rgba(239, 68, 68, 0.05)" }}>
                                <h3 className="stage-title flex items-center gap-2 text-red-400">
                                    <ShieldAlert size={16} />
                                    Action Required ({actionItems.length})
                                </h3>
                            </div>
                            <div className="stage-tracks">
                                <AnimatePresence mode="popLayout">
                                    {actionItems.map(track => {
                                        const program = programs.find(p => p.id === track.program_id);
                                        return (
                                            <motion.div
                                                key={track.id}
                                                className="pipeline-track-row"
                                                onClick={() => selectProgram(track.program_id)}
                                                variants={rowVariants}
                                                initial="hidden"
                                                animate="visible"
                                                exit="exit"
                                                layout
                                            >
                                                <div className="track-info">
                                                    <div className="status-indicator indicator--blocked" />
                                                    <div className="track-text">
                                                        <span className="track-program">{program?.title || track.program_id}</span>
                                                        <span className="track-language flex items-center gap-1">
                                                            {track.language_name} • <span style={{ color: "#f87171" }}>{track.stage.replace(/_/g, " ")}</span>
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
                                            </motion.div>
                                        );
                                    })}
                                </AnimatePresence>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* ─── ACTIVE PROCESSING ─────────────────── */}
                <div className="stage-group">
                    <div className="stage-header">
                        <h3 className="stage-title flex items-center gap-2">
                            <Zap size={16} style={{ color: "#60a5fa" }} />
                            Active Processing ({activeProcessing.length})
                        </h3>
                    </div>
                    <div className="stage-tracks">
                        {activeProcessing.length === 0 ? (
                            <div className="qc-empty">No tracks are currently passing through the pipeline.</div>
                        ) : (
                            <AnimatePresence mode="popLayout">
                                {activeProcessing.map(track => {
                                    const program = programs.find(p => p.id === track.program_id);
                                    return (
                                        <motion.div
                                            key={track.id}
                                            className="pipeline-track-row"
                                            onClick={() => selectProgram(track.program_id)}
                                            variants={rowVariants}
                                            initial="hidden"
                                            animate="visible"
                                            exit="exit"
                                            layout
                                        >
                                            <div className="track-info">
                                                <div className="status-indicator indicator--active" />
                                                <div className="track-text">
                                                    <span className="track-program">{program?.title || track.program_id}</span>
                                                    <span className="track-language">{track.language_name} • {track.type.toUpperCase()}</span>
                                                </div>
                                            </div>

                                            <div className="track-progress flex flex-col gap-1 w-full" style={{ alignItems: "flex-start", paddingTop: "4px", paddingBottom: "4px" }}>
                                                <div className="flex justify-between text-xs text-muted mb-1 w-full">
                                                    <span>{track.stage}</span>
                                                    <span className="tabular-nums font-mono">{track.progress.toFixed(0)}%</span>
                                                </div>
                                                <div style={{ height: "4px", width: "100%", background: "rgba(255,255,255,0.1)", borderRadius: "4px", overflow: "hidden" }}>
                                                    <div style={{ height: "100%", background: "#3b82f6", width: `${Math.max(2, track.progress)}%`, transition: "width 0.5s ease" }} />
                                                </div>
                                                <span style={{ fontSize: "10px" }} className="text-muted truncate mt-1">{track.status}</span>
                                            </div>

                                            <div className="track-status-badge flex items-center justify-end">
                                                <div className="flex items-center gap-2 text-xs font-medium text-blue-400">
                                                    <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                                                    Processing
                                                </div>
                                            </div>
                                        </motion.div>
                                    );
                                })}
                            </AnimatePresence>
                        )}
                    </div>
                </div>

                {/* ─── RECENTLY COMPLETED ────────────────── */}
                {recentDeliveries.length > 0 && (
                    <motion.div
                        className="stage-group"
                        style={{ borderColor: "rgba(34, 197, 94, 0.25)", boxShadow: "0 0 20px rgba(34, 197, 94, 0.04)" }}
                        initial={{ opacity: 0, y: 12 }}
                        animate={{ opacity: 1, y: 0, transition: { delay: 0.15 } }}
                    >
                        <div className="stage-header" style={{ background: "rgba(34, 197, 94, 0.04)" }}>
                            <h3 className="stage-title flex items-center gap-2" style={{ color: "#4ade80" }}>
                                <CheckCircle size={16} />
                                Recently Completed ({recentDeliveries.length})
                            </h3>
                        </div>
                        <div className="stage-tracks">
                            <AnimatePresence mode="popLayout">
                                {recentDeliveries.map((del, i) => {
                                    const program = programs.find(p =>
                                        p.tracks.some(t => t.id === del.track_id)
                                    );
                                    return (
                                        <motion.div
                                            key={del.id}
                                            className="pipeline-track-row"
                                            onClick={() => program && selectProgram(program.id)}
                                            style={{ cursor: program ? "pointer" : "default" }}
                                            variants={rowVariants}
                                            initial="hidden"
                                            animate="visible"
                                            exit="exit"
                                            layout
                                        >
                                            <div className="track-info">
                                                <div className="status-indicator indicator--complete" />
                                                <div className="track-text">
                                                    <span className="track-program">{del.program_title || program?.title || "—"}</span>
                                                    <span className="track-language">
                                                        {del.language_code?.toUpperCase() || "SUB"} • {del.destination}
                                                    </span>
                                                </div>
                                            </div>
                                            <div className="track-progress">
                                                <span className="text-muted text-xs">{del.notes || ""}</span>
                                            </div>
                                            <div className="track-status-badge flex items-center justify-end">
                                                <span className="text-xs" style={{ color: "#4ade80" }}>
                                                    {formatRelativeDate(del.delivered_at)}
                                                </span>
                                            </div>
                                        </motion.div>
                                    );
                                })}
                            </AnimatePresence>
                        </div>
                    </motion.div>
                )}
            </div>
        </div>
    );
}
