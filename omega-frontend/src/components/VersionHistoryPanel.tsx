"use client";

import { useState } from "react";
import { History, RotateCcw, ChevronDown, ChevronRight, Clock } from "lucide-react";

// =============================================================================
// Types
// =============================================================================

export interface HistoryEntry {
    id: string;
    timestamp: Date;
    action: string;
    segmentCount: number;
    changedSegments?: number[];
    description?: string;
}

interface Segment {
    id?: number;
    start: number;
    end: number;
    text: string;
}

// =============================================================================
// VersionHistoryPanel Component
// =============================================================================

interface VersionHistoryPanelProps {
    history: Segment[][];
    currentSegments: Segment[];
    onRevert: (index: number) => void;
}

function formatTimeAgo(date: Date): string {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);

    if (seconds < 60) return "Just now";
    if (seconds < 120) return "1 minute ago";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`;
    if (seconds < 7200) return "1 hour ago";
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`;
    return date.toLocaleDateString();
}

function describeChange(prev: Segment[], current: Segment[]): string {
    if (!prev || !current) return "Initial state";

    const prevCount = prev.length;
    const currentCount = current.length;

    if (currentCount > prevCount) {
        return `Added ${currentCount - prevCount} segment${currentCount - prevCount > 1 ? "s" : ""}`;
    }
    if (currentCount < prevCount) {
        return `Removed ${prevCount - currentCount} segment${prevCount - currentCount > 1 ? "s" : ""}`;
    }

    // Same count - check for text changes
    let textChanges = 0;
    let timeChanges = 0;

    for (let i = 0; i < Math.min(prev.length, current.length); i++) {
        if (prev[i].text !== current[i].text) textChanges++;
        if (prev[i].start !== current[i].start || prev[i].end !== current[i].end) timeChanges++;
    }

    if (textChanges > 0 && timeChanges > 0) {
        return `Modified ${textChanges} text + ${timeChanges} timing`;
    }
    if (textChanges > 0) {
        return `Modified ${textChanges} text${textChanges > 1 ? "s" : ""}`;
    }
    if (timeChanges > 0) {
        return `Adjusted ${timeChanges} timing${timeChanges > 1 ? "s" : ""}`;
    }

    return "State saved";
}

export function VersionHistoryPanel({ history, currentSegments, onRevert }: VersionHistoryPanelProps) {
    const [expanded, setExpanded] = useState(true);

    if (history.length === 0) {
        return (
            <div style={{ padding: 16, background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#6b7280" }}>
                    <History style={{ width: 14, height: 14 }} />
                    <span style={{ fontSize: 12 }}>No edit history yet</span>
                </div>
                <p style={{ fontSize: 11, color: "#6b7280", margin: "8px 0 0 0", fontStyle: "italic" }}>
                    Start editing to create history points
                </p>
            </div>
        );
    }

    // Create history entries with timestamps (approximate - based on array order)
    const entries = history.map((state, index) => {
        const prevState = index > 0 ? history[index - 1] : [];
        const minutesAgo = (history.length - index) * 2; // Approximate 2 min intervals

        return {
            index,
            timestamp: new Date(Date.now() - minutesAgo * 60000),
            description: describeChange(prevState, state),
            segmentCount: state.length,
        };
    }).reverse(); // Show most recent first

    return (
        <div style={{ padding: 16, background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
            <button
                onClick={() => setExpanded(!expanded)}
                style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    color: "#f5f5f5",
                    padding: 0,
                }}
            >
                {expanded ? <ChevronDown style={{ width: 14, height: 14, color: "#6b7280" }} /> : <ChevronRight style={{ width: 14, height: 14, color: "#6b7280" }} />}
                <History style={{ width: 14, height: 14, color: "#9ca3af" }} />
                <span style={{ flex: 1, textAlign: "left", fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                    Version History
                </span>
                <span style={{ fontSize: 11, color: "#6b7280" }}>{history.length}</span>
            </button>

            {expanded && (
                <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                    {/* Current State */}
                    <div
                        style={{
                            padding: "8px 10px",
                            background: "rgba(82,139,255,0.1)",
                            borderRadius: 6,
                            border: "1px solid rgba(82,139,255,0.2)",
                        }}
                    >
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e" }} />
                            <span style={{ fontSize: 11, fontWeight: 600, color: "#f5f5f5" }}>Current</span>
                            <span style={{ fontSize: 10, color: "#6b7280", marginLeft: "auto" }}>
                                {currentSegments.length} segments
                            </span>
                        </div>
                    </div>

                    {/* History Entries */}
                    {entries.slice(0, 10).map((entry) => (
                        <div
                            key={entry.index}
                            style={{
                                padding: "8px 10px",
                                background: "rgba(255,255,255,0.02)",
                                borderRadius: 6,
                                border: "1px solid rgba(255,255,255,0.05)",
                                display: "flex",
                                flexDirection: "column",
                                gap: 4,
                            }}
                        >
                            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                <Clock style={{ width: 10, height: 10, color: "#6b7280" }} />
                                <span style={{ fontSize: 10, color: "#9ca3af" }}>
                                    {formatTimeAgo(entry.timestamp)}
                                </span>
                                <span style={{ fontSize: 10, color: "#6b7280", marginLeft: "auto" }}>
                                    {entry.segmentCount} seg
                                </span>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                                <span style={{ fontSize: 11, color: "#9ca3af" }}>{entry.description}</span>
                                <button
                                    onClick={() => onRevert(entry.index)}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 4,
                                        padding: "3px 8px",
                                        fontSize: 10,
                                        fontWeight: 500,
                                        color: "#f59e0b",
                                        background: "rgba(245,158,11,0.1)",
                                        border: "1px solid rgba(245,158,11,0.2)",
                                        borderRadius: 4,
                                        cursor: "pointer",
                                    }}
                                >
                                    <RotateCcw style={{ width: 10, height: 10 }} />
                                    Revert
                                </button>
                            </div>
                        </div>
                    ))}

                    {entries.length > 10 && (
                        <div style={{ fontSize: 10, color: "#6b7280", textAlign: "center", padding: 4 }}>
                            +{entries.length - 10} older versions
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
