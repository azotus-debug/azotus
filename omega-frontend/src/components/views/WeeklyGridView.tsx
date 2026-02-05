"use client";

import { CSSProperties, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X, Download } from "lucide-react";
import PageHeader from "@/components/layout/PageHeader";
import Badge from "@/components/common/Badge";
import Button from "@/components/common/Button";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

// Types matching the API response
interface Output {
    type: string;
    filename: string;
    status: string;
    size: string;
    url: string;
    error_msg?: string;
}

interface Track {
    track_id?: string;
    status: string;
    status_label: string;
    outputs_total?: number;
    outputs_ready?: number;
    has_error?: boolean;
    outputs?: Output[];
}

interface Program {
    program_id: string;
    title: string;
    client_id: string;
    air_date: string;
    thumbnail?: string;
    tracks: Record<string, Track>;
}

interface GridData {
    week_label: string;
    programs: Program[];
}

interface SelectedTrack {
    track: Track;
    programTitle: string;
    language: string;
    languageCode: string;
    programId: string;
}

// Language configuration
const LANGUAGES = [
    { code: "is", name: "Icelandic" },
    { code: "es", name: "Spanish" },
    { code: "de", name: "German" },
    { code: "en", name: "English" },
];

// Utility: Generate a hue from string for client colors
const hashString = (value: string): number => {
    let hash = 0;
    for (let i = 0; i < value.length; i++) {
        hash = value.charCodeAt(i) + ((hash << 5) - hash);
    }
    return hash;
};

const clientStyleFromName = (value: string): CSSProperties => {
    const hue = Math.abs(hashString(value)) % 360;
    return { "--client-hue": `${hue}` } as CSSProperties;
};

// Get cell variant based on track status
const getTrackCellClass = (track?: Track): string => {
    if (!track || track.status === "MISSING") return "track-cell--missing";

    const status = track.status.toUpperCase();

    if (track.has_error || status === "ERROR" || status === "FAILED") {
        return "track-cell--error";
    }
    if (status === "COMPLETED" || status === "APPROVED" || status === "DELIVERED") {
        return "track-cell--complete";
    }
    if (status === "AWAITING_REVIEW" || status === "REVIEWING" || status === "AWAITING_APPROVAL") {
        return "track-cell--review";
    }
    if (status === "QUEUED") {
        return "track-cell--queued";
    }
    // Active states: transcribing, translating, burning, etc.
    return "track-cell--active";
};

// Get panel status class
const getPanelStatusClass = (track: Track): string => {
    const status = track.status.toUpperCase();
    if (track.has_error || status === "ERROR" || status === "FAILED") {
        return "track-panel-status--error";
    }
    if (status === "COMPLETED" || status === "APPROVED" || status === "DELIVERED") {
        return "track-panel-status--complete";
    }
    if (status === "AWAITING_REVIEW" || status === "REVIEWING" || status === "AWAITING_APPROVAL") {
        return "track-panel-status--review";
    }
    return "track-panel-status--active";
};

// Get output status class
const getOutputStatusClass = (status: string): string => {
    const s = status.toUpperCase();
    if (s === "READY") return "output-status--ready";
    if (s === "FAILED") return "output-status--failed";
    return "output-status--pending";
};

export default function WeeklyGridView() {
    const [data, setData] = useState<GridData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedTrack, setSelectedTrack] = useState<SelectedTrack | null>(null);
    const [creating, setCreating] = useState(false);

    // Fetch grid data
    const fetchGridData = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/weekly_grid`, { cache: "no-store" });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const json = await res.json();
            setData(json);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchGridData();
    }, []);

    // Create a new track
    const handleCreateTrack = async (programId: string, langCode: string, langName: string) => {
        if (creating) return;

        const confirmed = window.confirm(`Create ${langName} track for this program?`);
        if (!confirmed) return;

        setCreating(true);
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ program_id: programId, language_code: langCode }),
            });
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.error || "Failed to create track");
            }
            // Refresh data
            await fetchGridData();
        } catch (e) {
            alert(e instanceof Error ? e.message : "Failed to create track");
        } finally {
            setCreating(false);
        }
    };

    // Handle cell click
    const handleCellClick = (
        program: Program,
        langCode: string,
        langName: string
    ) => {
        const track = program.tracks[langCode];

        if (!track || track.status === "MISSING") {
            handleCreateTrack(program.program_id, langCode, langName);
            return;
        }

        setSelectedTrack({
            track,
            programTitle: program.title,
            language: langName,
            languageCode: langCode,
            programId: program.program_id,
        });
    };

    // Close panel
    const closePanel = () => setSelectedTrack(null);

    // Loading state
    if (loading) {
        return (
            <section className="weekly-grid-view">
                <PageHeader title="Weekly Grid" subtitle="Loading..." />
                <div className="loading-spinner" />
            </section>
        );
    }

    // Error state
    if (error) {
        return (
            <section className="weekly-grid-view">
                <PageHeader title="Weekly Grid" subtitle="Error loading data" />
                <div className="error-message">{error}</div>
            </section>
        );
    }

    const programCount = data?.programs?.length || 0;

    return (
        <section className="weekly-grid-view">
            <PageHeader
                title="Weekly Grid"
                subtitle={`${programCount} program${programCount !== 1 ? "s" : ""}`}
            />

            {/* Controls */}
            <div className="weekly-grid-controls">
                <div className="week-nav">
                    <button type="button" className="week-nav-btn" title="Previous Week">
                        <ChevronLeft size={16} />
                    </button>
                    <span className="week-label">{data?.week_label || "All Programs"}</span>
                    <button type="button" className="week-nav-btn" title="Next Week">
                        <ChevronRight size={16} />
                    </button>
                </div>
            </div>

            {/* Grid Table */}
            {programCount === 0 ? (
                <div className="empty-state">
                    <span style={{ fontSize: "48px" }}>📋</span>
                    <p>No programs found for this period.</p>
                </div>
            ) : (
                <div className="weekly-grid-container">
                    <table className="weekly-grid-table">
                        <thead className="weekly-grid-thead">
                            <tr>
                                <th className="weekly-grid-th">Program</th>
                                {LANGUAGES.map((lang) => (
                                    <th key={lang.code} className="weekly-grid-th weekly-grid-th--lang">
                                        {lang.name}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data?.programs.map((program) => (
                                <tr key={program.program_id} className="weekly-grid-row">
                                    {/* Program Info Cell */}
                                    <td className="weekly-grid-td">
                                        <div className="program-info">
                                            <span className="program-title">{program.title}</span>
                                            <div className="program-meta">
                                                {program.client_id && program.client_id !== "unknown" && (
                                                    <span
                                                        className="program-client"
                                                        style={clientStyleFromName(program.client_id)}
                                                    >
                                                        {program.client_id}
                                                    </span>
                                                )}
                                                {program.air_date && (
                                                    <span className="program-date">{program.air_date}</span>
                                                )}
                                            </div>
                                        </div>
                                    </td>

                                    {/* Language Cells */}
                                    {LANGUAGES.map((lang) => {
                                        const track = program.tracks[lang.code];
                                        const cellClass = getTrackCellClass(track);
                                        const isSelected =
                                            selectedTrack?.programId === program.program_id &&
                                            selectedTrack?.languageCode === lang.code;

                                        return (
                                            <td key={lang.code} className="weekly-grid-td weekly-grid-td--lang">
                                                <button
                                                    type="button"
                                                    className={`track-cell ${cellClass}${isSelected ? " track-cell--selected" : ""}`}
                                                    onClick={() => handleCellClick(program, lang.code, lang.name)}
                                                    disabled={creating}
                                                >
                                                    {!track || track.status === "MISSING"
                                                        ? "+ Create"
                                                        : track.status_label || track.status}
                                                </button>
                                            </td>
                                        );
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Track Detail Panel */}
            <div
                className={`track-panel-overlay${selectedTrack ? " open" : ""}`}
                onClick={closePanel}
            />
            <aside className={`track-panel${selectedTrack ? " open" : ""}`}>
                {selectedTrack && (
                    <>
                        {/* Panel Header */}
                        <div className="track-panel-header">
                            <div className="track-panel-header-row">
                                <div>
                                    <h2 className="track-panel-title">{selectedTrack.programTitle}</h2>
                                    <p className="track-panel-subtitle">
                                        {selectedTrack.language} • {selectedTrack.track.status_label}
                                    </p>
                                </div>
                                <button
                                    type="button"
                                    className="track-panel-close"
                                    onClick={closePanel}
                                    aria-label="Close panel"
                                >
                                    <X size={16} />
                                </button>
                            </div>

                            {/* Status Banner */}
                            <div className={`track-panel-status ${getPanelStatusClass(selectedTrack.track)}`}>
                                <span className="track-panel-status-text">
                                    {selectedTrack.track.has_error
                                        ? "Needs Attention"
                                        : selectedTrack.track.status_label || selectedTrack.track.status}
                                </span>
                            </div>
                        </div>

                        {/* Panel Content */}
                        <div className="track-panel-content">
                            {/* Deliverables Section */}
                            <div className="track-panel-section">
                                <h3 className="track-panel-section-title">Deliverables</h3>
                                {selectedTrack.track.outputs && selectedTrack.track.outputs.length > 0 ? (
                                    selectedTrack.track.outputs.map((output, idx) => (
                                        <div key={idx} className="output-card">
                                            <div className="output-card-header">
                                                <span className="output-card-title">
                                                    {output.type || output.filename || "Output"}
                                                </span>
                                                {output.status === "READY" && output.url && (
                                                    <button
                                                        type="button"
                                                        className="download-btn"
                                                        title="Download"
                                                    >
                                                        <Download size={14} />
                                                    </button>
                                                )}
                                            </div>
                                            {output.filename && (
                                                <p className="output-card-filename">{output.filename}</p>
                                            )}
                                            <div className="output-card-footer">
                                                <span className={`output-status ${getOutputStatusClass(output.status)}`}>
                                                    {output.status}
                                                </span>
                                                {output.error_msg && (
                                                    <Badge label={output.error_msg.slice(0, 30)} variant="error" />
                                                )}
                                                {output.size && <span className="output-size">{output.size}</span>}
                                            </div>
                                        </div>
                                    ))
                                ) : (
                                    <div className="empty-outputs">No deliverables yet.</div>
                                )}
                            </div>

                            {/* Activity Section (placeholder) */}
                            <div className="track-panel-section">
                                <h3 className="track-panel-section-title">Recent Activity</h3>
                                <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                                    Activity timeline coming soon.
                                </p>
                            </div>
                        </div>

                        {/* Panel Footer */}
                        <div className="track-panel-footer">
                            <Button variant="secondary">View Full Details</Button>
                        </div>
                    </>
                )}
            </aside>
        </section>
    );
}
