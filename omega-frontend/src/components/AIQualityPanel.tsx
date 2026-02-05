"use client";

import { useState, useEffect } from "react";
import { AlertTriangle, CheckCircle2, Star, ChevronDown, ChevronRight } from "lucide-react";

// =============================================================================
// Types
// =============================================================================

interface FlaggedSegment {
    id: number;
    reason: string;
    severity?: "low" | "medium" | "high";
    original?: string;
    suggested?: string;
}

interface EditorReportData {
    summary?: string;
    quality_score?: number;
    rating?: number;
    quality_tier?: string;
    notes?: string[];
    corrections_count?: number;
    flagged_segments?: FlaggedSegment[];
    [key: string]: unknown;
}

// =============================================================================
// Helpers
// =============================================================================

function getQualityColor(score: number): string {
    if (score >= 9) return "#22c55e";
    if (score >= 7) return "#3b82f6";
    if (score >= 5) return "#f59e0b";
    return "#ef4444";
}

function getQualityEmoji(score: number): string {
    if (score >= 9) return "✨";
    if (score >= 8) return "🎯";
    if (score >= 7) return "👍";
    if (score >= 5) return "📝";
    return "⚠️";
}

function getSeverityColor(severity?: string): string {
    switch (severity) {
        case "high": return "#ef4444";
        case "medium": return "#f59e0b";
        default: return "#6b7280";
    }
}

// =============================================================================
// AIQualityPanel Component
// =============================================================================

interface AIQualityPanelProps {
    jobId: string;
    onJumpToSegment?: (segmentId: number) => void;
}

export function AIQualityPanel({ jobId, onJumpToSegment }: AIQualityPanelProps) {
    const [report, setReport] = useState<EditorReportData | null>(null);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState(true);
    const [flagsExpanded, setFlagsExpanded] = useState(true);

    useEffect(() => {
        async function fetchReport() {
            try {
                const res = await fetch(`/api/jobs`);
                const jobs = await res.json();
                const job = jobs.find((j: { file_stem: string }) => j.file_stem === jobId);

                if (job?.editor_report) {
                    const parsed = typeof job.editor_report === "string"
                        ? JSON.parse(job.editor_report)
                        : job.editor_report;
                    setReport(parsed);
                }
            } catch (e) {
                console.error("Failed to fetch editor report:", e);
            } finally {
                setLoading(false);
            }
        }

        fetchReport();
    }, [jobId]);

    if (loading) {
        return (
            <div style={{ padding: 16, color: "#6b7280", fontSize: 12 }}>
                Loading quality data...
            </div>
        );
    }

    if (!report) {
        return (
            <div style={{ padding: 16, color: "#6b7280", fontSize: 12, fontStyle: "italic" }}>
                No quality report available
            </div>
        );
    }

    const score = report.quality_score ?? report.rating;
    const tier = report.quality_tier;
    const notes = report.notes || [];
    const flaggedSegments = report.flagged_segments || [];

    return (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {/* Quality Score Section */}
            {score !== undefined && (
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
                        <Star style={{ width: 14, height: 14, color: getQualityColor(score) }} />
                        <span style={{ flex: 1, textAlign: "left", fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                            AI Quality Report
                        </span>
                    </button>

                    {expanded && (
                        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 12 }}>
                            {/* Score Circle */}
                            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                                <div
                                    style={{
                                        width: 56,
                                        height: 56,
                                        borderRadius: "50%",
                                        display: "flex",
                                        flexDirection: "column",
                                        alignItems: "center",
                                        justifyContent: "center",
                                        background: `${getQualityColor(score)}15`,
                                        border: `2px solid ${getQualityColor(score)}`,
                                    }}
                                >
                                    <span style={{ fontSize: 18, fontWeight: 700, color: getQualityColor(score) }}>
                                        {score.toFixed(1)}
                                    </span>
                                </div>
                                <div>
                                    <div style={{ fontSize: 14, fontWeight: 600, color: "#f5f5f5" }}>
                                        {getQualityEmoji(score)} {tier || (score >= 8 ? "Broadcast Ready" : score >= 6 ? "Good" : "Needs Review")}
                                    </div>
                                    {report.corrections_count !== undefined && (
                                        <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 2 }}>
                                            {report.corrections_count} AI corrections applied
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Summary */}
                            {report.summary && (
                                <p style={{ fontSize: 12, color: "#9ca3af", margin: 0, lineHeight: 1.5 }}>
                                    {report.summary}
                                </p>
                            )}

                            {/* Notes */}
                            {notes.length > 0 && (
                                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                    <div style={{ fontSize: 11, fontWeight: 600, color: "#6b7280", textTransform: "uppercase" }}>Notes</div>
                                    {notes.slice(0, 5).map((note, i) => (
                                        <div
                                            key={i}
                                            style={{
                                                fontSize: 11,
                                                color: "#9ca3af",
                                                padding: "6px 10px",
                                                background: "rgba(255,255,255,0.03)",
                                                borderRadius: 4,
                                                borderLeft: "2px solid #528BFF",
                                            }}
                                        >
                                            {note}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Flagged Segments Section */}
            {flaggedSegments.length > 0 && (
                <div style={{ padding: 16, background: "rgba(245,158,11,0.08)", borderRadius: 8, border: "1px solid rgba(245,158,11,0.2)" }}>
                    <button
                        onClick={() => setFlagsExpanded(!flagsExpanded)}
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
                        {flagsExpanded ? <ChevronDown style={{ width: 14, height: 14, color: "#f59e0b" }} /> : <ChevronRight style={{ width: 14, height: 14, color: "#f59e0b" }} />}
                        <AlertTriangle style={{ width: 14, height: 14, color: "#f59e0b" }} />
                        <span style={{ flex: 1, textAlign: "left", fontSize: 12, fontWeight: 600, color: "#f59e0b" }}>
                            {flaggedSegments.length} Flagged Segment{flaggedSegments.length > 1 ? "s" : ""}
                        </span>
                    </button>

                    {flagsExpanded && (
                        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                            {flaggedSegments.slice(0, 10).map((flag) => (
                                <button
                                    key={flag.id}
                                    onClick={() => onJumpToSegment?.(flag.id)}
                                    style={{
                                        width: "100%",
                                        padding: "8px 10px",
                                        background: "rgba(255,255,255,0.03)",
                                        borderRadius: 6,
                                        border: `1px solid ${getSeverityColor(flag.severity)}30`,
                                        cursor: "pointer",
                                        textAlign: "left",
                                        display: "flex",
                                        flexDirection: "column",
                                        gap: 4,
                                    }}
                                >
                                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                        <span style={{ fontSize: 11, fontWeight: 600, color: getSeverityColor(flag.severity) }}>
                                            #{flag.id}
                                        </span>
                                        <span style={{ fontSize: 11, color: "#9ca3af" }}>
                                            {flag.reason}
                                        </span>
                                    </div>
                                    {flag.suggested && (
                                        <div style={{ fontSize: 10, color: "#6b7280" }}>
                                            Suggested: <span style={{ color: "#22c55e" }}>{flag.suggested}</span>
                                        </div>
                                    )}
                                </button>
                            ))}
                            {flaggedSegments.length > 10 && (
                                <div style={{ fontSize: 11, color: "#6b7280", textAlign: "center" }}>
                                    +{flaggedSegments.length - 10} more
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* All Clear Badge */}
            {score !== undefined && score >= 8 && flaggedSegments.length === 0 && (
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "10px 12px",
                        background: "rgba(34,197,94,0.1)",
                        borderRadius: 8,
                        border: "1px solid rgba(34,197,94,0.2)",
                    }}
                >
                    <CheckCircle2 style={{ width: 16, height: 16, color: "#22c55e" }} />
                    <span style={{ fontSize: 12, fontWeight: 500, color: "#22c55e" }}>
                        Ready for broadcast
                    </span>
                </div>
            )}
        </div>
    );
}
