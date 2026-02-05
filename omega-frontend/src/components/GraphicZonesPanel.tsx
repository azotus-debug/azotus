"use client";

import { useState, useCallback } from "react";
import { MapPin, Plus, Trash2, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";

// =============================================================================
// Types
// =============================================================================

export interface GraphicZone {
    id: string;
    startTime: number;
    endTime: number;
    label: string;
    position: "top" | "bottom"; // Where subtitles should go during this zone
}

interface GraphicZonesPanelProps {
    zones: GraphicZone[];
    currentTime: number;
    onZonesChange: (zones: GraphicZone[]) => void;
    onSeekTo: (time: number) => void;
}

// =============================================================================
// Helpers
// =============================================================================

function formatTimecode(seconds: number): string {
    if (!Number.isFinite(seconds)) return "00:00:00";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [h, m, s].map((v) => v.toString().padStart(2, "0")).join(":");
}


// =============================================================================
// GraphicZonesPanel Component
// =============================================================================

export function GraphicZonesPanel({ zones, currentTime, onZonesChange, onSeekTo }: GraphicZonesPanelProps) {
    const [expanded, setExpanded] = useState(true);
    const [isMarkingZone, setIsMarkingZone] = useState(false);
    const [pendingZoneStart, setPendingZoneStart] = useState<number | null>(null);

    // Start marking a new zone
    const startZone = useCallback(() => {
        setPendingZoneStart(currentTime);
        setIsMarkingZone(true);
    }, [currentTime]);

    // End the current zone
    const endZone = useCallback(() => {
        if (pendingZoneStart === null) return;

        const newZone: GraphicZone = {
            id: `zone-${Date.now()}`,
            startTime: Math.min(pendingZoneStart, currentTime),
            endTime: Math.max(pendingZoneStart, currentTime),
            label: `Graphic ${zones.length + 1}`,
            position: "top",
        };

        onZonesChange([...zones, newZone].sort((a, b) => a.startTime - b.startTime));
        setPendingZoneStart(null);
        setIsMarkingZone(false);
    }, [pendingZoneStart, currentTime, zones, onZonesChange]);

    // Delete a zone
    const deleteZone = useCallback((id: string) => {
        onZonesChange(zones.filter((z) => z.id !== id));
    }, [zones, onZonesChange]);

    // Update zone label
    const updateLabel = useCallback((id: string, label: string) => {
        onZonesChange(zones.map((z) => (z.id === id ? { ...z, label } : z)));
    }, [zones, onZonesChange]);

    // Check if current time is in any zone
    const activeZone = zones.find((z) => currentTime >= z.startTime && currentTime <= z.endTime);

    return (
        <div style={{ padding: 16, background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
            {/* Header */}
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
                <MapPin style={{ width: 14, height: 14, color: "#f59e0b" }} />
                <span style={{ flex: 1, textAlign: "left", fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                    Graphic Zones
                </span>
                <span style={{ fontSize: 11, color: "#6b7280" }}>{zones.length}</span>
            </button>

            {expanded && (
                <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* Active Zone Indicator */}
                    {activeZone && (
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                                padding: "8px 12px",
                                background: "rgba(245,158,11,0.15)",
                                borderRadius: 6,
                                border: "1px solid rgba(245,158,11,0.3)",
                            }}
                        >
                            <ArrowUp style={{ width: 14, height: 14, color: "#f59e0b" }} />
                            <span style={{ fontSize: 11, fontWeight: 500, color: "#f59e0b" }}>
                                Subtitles → TOP (in zone)
                            </span>
                        </div>
                    )}

                    {/* Mark Zone Buttons */}
                    <div style={{ display: "flex", gap: 8 }}>
                        {!isMarkingZone ? (
                            <button
                                onClick={startZone}
                                style={{
                                    flex: 1,
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    gap: 6,
                                    padding: "10px 12px",
                                    fontSize: 11,
                                    fontWeight: 500,
                                    color: "#f5f5f5",
                                    background: "rgba(245,158,11,0.15)",
                                    border: "1px solid rgba(245,158,11,0.3)",
                                    borderRadius: 6,
                                    cursor: "pointer",
                                }}
                            >
                                <Plus style={{ width: 12, height: 12 }} />
                                Mark Zone Start
                            </button>
                        ) : (
                            <>
                                <div
                                    style={{
                                        flex: 1,
                                        padding: "10px 12px",
                                        fontSize: 11,
                                        color: "#22c55e",
                                        background: "rgba(34,197,94,0.1)",
                                        border: "1px solid rgba(34,197,94,0.3)",
                                        borderRadius: 6,
                                        textAlign: "center",
                                    }}
                                >
                                    🔴 Recording... ({formatTimecode(pendingZoneStart || 0)})
                                </div>
                                <button
                                    onClick={endZone}
                                    style={{
                                        padding: "10px 16px",
                                        fontSize: 11,
                                        fontWeight: 600,
                                        color: "#22c55e",
                                        background: "rgba(34,197,94,0.15)",
                                        border: "1px solid rgba(34,197,94,0.3)",
                                        borderRadius: 6,
                                        cursor: "pointer",
                                    }}
                                >
                                    End Zone
                                </button>
                            </>
                        )}
                    </div>

                    {/* Keyboard Hint */}
                    <div style={{ fontSize: 10, color: "#6b7280", textAlign: "center" }}>
                        Tip: Press <kbd style={{ padding: "2px 6px", background: "rgba(255,255,255,0.1)", borderRadius: 3, fontFamily: "monospace" }}>G</kbd> to toggle zone marking
                    </div>

                    {/* Zone List */}
                    {zones.length === 0 ? (
                        <div style={{ fontSize: 11, color: "#6b7280", fontStyle: "italic", textAlign: "center", padding: 8 }}>
                            No graphic zones marked yet
                        </div>
                    ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {zones.map((zone) => (
                                <div
                                    key={zone.id}
                                    style={{
                                        padding: 10,
                                        background: "rgba(255,255,255,0.02)",
                                        borderRadius: 6,
                                        border: `1px solid ${activeZone?.id === zone.id ? "rgba(245,158,11,0.4)" : "rgba(255,255,255,0.05)"}`,
                                    }}
                                >
                                    {/* Timecodes */}
                                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                                        <button
                                            onClick={() => onSeekTo(zone.startTime)}
                                            style={{
                                                padding: "2px 6px",
                                                fontSize: 10,
                                                fontFamily: "monospace",
                                                color: "#22c55e",
                                                background: "rgba(34,197,94,0.1)",
                                                border: "1px solid rgba(34,197,94,0.2)",
                                                borderRadius: 3,
                                                cursor: "pointer",
                                            }}
                                        >
                                            {formatTimecode(zone.startTime)}
                                        </button>
                                        <span style={{ fontSize: 10, color: "#6b7280" }}>→</span>
                                        <button
                                            onClick={() => onSeekTo(zone.endTime)}
                                            style={{
                                                padding: "2px 6px",
                                                fontSize: 10,
                                                fontFamily: "monospace",
                                                color: "#ef4444",
                                                background: "rgba(239,68,68,0.1)",
                                                border: "1px solid rgba(239,68,68,0.2)",
                                                borderRadius: 3,
                                                cursor: "pointer",
                                            }}
                                        >
                                            {formatTimecode(zone.endTime)}
                                        </button>
                                        <span style={{ flex: 1 }} />
                                        <button
                                            onClick={() => deleteZone(zone.id)}
                                            style={{
                                                padding: 4,
                                                color: "#6b7280",
                                                background: "transparent",
                                                border: "none",
                                                cursor: "pointer",
                                            }}
                                        >
                                            <Trash2 style={{ width: 12, height: 12 }} />
                                        </button>
                                    </div>

                                    {/* Label */}
                                    <input
                                        type="text"
                                        value={zone.label}
                                        onChange={(e) => updateLabel(zone.id, e.target.value)}
                                        placeholder="Label (e.g., 'Pastor Name')"
                                        style={{
                                            width: "100%",
                                            padding: "4px 8px",
                                            fontSize: 11,
                                            color: "#f5f5f5",
                                            background: "rgba(0,0,0,0.2)",
                                            border: "1px solid rgba(255,255,255,0.1)",
                                            borderRadius: 4,
                                            outline: "none",
                                        }}
                                    />

                                    {/* Position Badge */}
                                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
                                        <ArrowUp style={{ width: 10, height: 10, color: "#f59e0b" }} />
                                        <span style={{ fontSize: 10, color: "#f59e0b" }}>Subtitles move to TOP</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// =============================================================================
// Helper: Check if a segment overlaps with any zone
// =============================================================================

export function getSegmentPosition(
    segmentStart: number,
    segmentEnd: number,
    zones: GraphicZone[]
): "bottom" | "top" {
    for (const zone of zones) {
        // Check if segment overlaps with zone
        if (segmentStart < zone.endTime && segmentEnd > zone.startTime) {
            return zone.position;
        }
    }
    return "bottom"; // Default position
}

// =============================================================================
// Helper: Apply position tags to ASS format
// =============================================================================

export function applyPositionToASS(text: string, position: "bottom" | "top"): string {
    if (position === "top") {
        return `{\\an8}${text}`; // \an8 = top-center alignment in ASS
    }
    return text; // Default bottom-center
}
