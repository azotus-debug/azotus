"use client";

import { useState, useEffect } from "react";
import { X, Download } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

// Types
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
    tracks: {
        [lang: string]: Track;
    };
}

interface GridData {
    week_label: string;
    programs: Program[];
}

interface SelectedTrack {
    track: Track;
    progTitle: string;
    lang: string;
}

export default function WeeklyGridWithMock() {
    const [data, setData] = useState<GridData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedTrack, setSelectedTrack] = useState<SelectedTrack | null>(null);

    useEffect(() => {
        let active = true;
        const fetchData = async () => {
            try {
                // Fetch from Backend API
                const response = await fetch(`${API_BASE}/api/v2/weekly_grid`, { cache: "no-store" });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const jsonData = (await response.json()) as GridData;
                if (!active) {
                    return;
                }
                setData(jsonData);
            } catch (e) {
                if (!active) {
                    return;
                }
                const message = e instanceof Error ? e.message : "Unknown error";
                setError(message);
            } finally {
                if (active) {
                    setLoading(false);
                }
            }
        };
        fetchData();
        return () => {
            active = false;
        };
    }, []);

    const createTrack = async (programId: string, lang: string) => {
        if (!confirm(`Create ${lang} track?`)) return;

        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ program_id: programId, language_code: lang.toLowerCase() })
            });
            if (!res.ok) throw new Error(await res.text());

            // Refresh
            const gridRes = await fetch(`${API_BASE}/api/v2/weekly_grid`);
            const gridJson = await gridRes.json();
            setData(gridJson);
        } catch (e) {
            alert("Failed to create track: " + e);
        }
    };

    const handleCellClick = (track: Track | undefined, progTitle: string, lang: string, programId: string) => {
        if (!track || track.status === 'MISSING') {
            createTrack(programId, lang);
            return;
        }
        setSelectedTrack({ track, progTitle, lang });
    };

    if (loading) {
        return (
            <div className="min-h-screen bg-[#0f0f0f] text-white flex items-center justify-center">
                Loading Grid...
            </div>
        );
    }

    if (error) {
        return (
            <div className="min-h-screen bg-[#0f0f0f] text-white flex items-center justify-center">
                Failed to load weekly grid: {error}
            </div>
        );
    }

    if (!data) {
        return (
            <div className="min-h-screen bg-[#0f0f0f] text-white flex items-center justify-center">
                No data available.
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-[#0f0f0f] text-white font-sans flex">
            <div className={`flex-1 transition-all duration-300 ${selectedTrack ? 'mr-96' : ''}`}>
                <div className="p-8">
                    {/* Header */}
                    <div className="flex justify-between items-end mb-8 border-b border-[#333] pb-4">
                        <div>
                            <h1 className="text-2xl font-bold tracking-tight mb-1">Weekly Production</h1>
                            <p className="text-sm text-[#888]">{data?.week_label}</p>
                        </div>
                        <div className="flex gap-2">
                            <button className="bg-[#222] hover:bg-[#333] text-white text-xs px-4 py-2 rounded-lg border border-[#333] transition-colors">Previous</button>
                            <button className="bg-[#222] hover:bg-[#333] text-white text-xs px-4 py-2 rounded-lg border border-[#333] transition-colors">Next</button>
                        </div>
                    </div>

                    {/* Grid */}
                    <div className="border border-[#333] rounded-xl overflow-hidden bg-[#1a1a1a]">
                        {/* Head */}
                        <div className="grid grid-cols-6 bg-[#222] border-b border-[#333] text-xs font-bold text-[#666] uppercase tracking-wider">
                            <div className="col-span-2 p-4">Program</div>
                            <div className="p-4 border-l border-[#333]">Icelandic</div>
                            <div className="p-4 border-l border-[#333]">Spanish</div>
                            <div className="p-4 border-l border-[#333]">German</div>
                            <div className="p-4 border-l border-[#333]">English</div>
                        </div>

                        {/* Body */}
                        {data?.programs.map((prog) => (
                            <div key={prog.program_id} className="grid grid-cols-6 border-b border-[#333] hover:bg-[#222] transition-colors group">
                                {/* Program Info */}
                                <div className="col-span-2 p-4 flex flex-col justify-center">
                                    <span className="font-medium text-white mb-1">{prog.title}</span>
                                    <div className="flex items-center gap-2">
                                        <span className="text-[10px] bg-[#333] text-[#aaa] px-1.5 py-0.5 rounded">{prog.client_id}</span>
                                        <span className="text-[10px] text-[#666]">{prog.air_date}</span>
                                    </div>
                                </div>

                                {/* Cells */}
                                <Cell track={prog.tracks['is']} onClick={() => handleCellClick(prog.tracks['is'], prog.title, 'Icelandic', prog.program_id)} isSelected={selectedTrack?.track.track_id === prog.tracks['is']?.track_id} />
                                <Cell track={prog.tracks['es']} onClick={() => handleCellClick(prog.tracks['es'], prog.title, 'Spanish', prog.program_id)} isSelected={selectedTrack?.track.track_id === prog.tracks['es']?.track_id} />
                                <Cell track={prog.tracks['de']} onClick={() => handleCellClick(prog.tracks['de'], prog.title, 'German', prog.program_id)} isSelected={selectedTrack?.track.track_id === prog.tracks['de']?.track_id} />
                                <Cell track={prog.tracks['en']} onClick={() => handleCellClick(prog.tracks['en'], prog.title, 'English', prog.program_id)} isSelected={selectedTrack?.track.track_id === prog.tracks['en']?.track_id} />
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Detail Drawer */}
            <div className={`fixed top-0 right-0 h-full w-96 bg-[#1a1a1a] border-l border-[#333] shadow-2xl transform transition-transform duration-300 ${selectedTrack ? 'translate-x-0' : 'translate-x-full'}`}>
                {selectedTrack && (
                    <div className="flex flex-col h-full">
                        {/* Drawer Header */}
                        <div className="p-6 border-b border-[#333] bg-[#222]">
                            <div className="flex items-start justify-between mb-4">
                                <div>
                                    <h2 className="text-lg font-bold text-white leading-tight mb-1">{selectedTrack.progTitle}</h2>
                                    <span className="text-xs font-medium text-[#888]">{selectedTrack.track.status_label} • {selectedTrack.lang}</span>
                                </div>
                                <button onClick={() => setSelectedTrack(null)} className="text-[#666] hover:text-white transition-colors">
                                    <X className="w-5 h-5" />
                                </button>
                            </div>
                            {/* Status Banner */}
                            {(() => {
                                const bannerStatus = selectedTrack.track.has_error ? "ERROR" : selectedTrack.track.status;
                                const bannerStyle = getTrackStatusStyles(bannerStatus);
                                const bannerClass = `${bannerStyle.className}${bannerStyle.pulse ? " animate-pulse" : ""}`;
                                return (
                                    <div className={`p-3 rounded-lg border ${bannerClass}`}>
                                        <p className="text-xs font-bold uppercase tracking-wider">
                                            {selectedTrack.track.status === "ERROR" ? "Needs Attention" : selectedTrack.track.status_label}
                                        </p>
                                    </div>
                                );
                            })()}
                        </div>

                        {/* Drawer Content */}
                        <div className="flex-1 overflow-y-auto p-6">
                            <h3 className="text-xs font-bold text-[#666] uppercase tracking-wider mb-4">Deliverables</h3>
                            <div className="space-y-3">
                                {selectedTrack.track.outputs && selectedTrack.track.outputs.length > 0 ? (
                                    selectedTrack.track.outputs.map((out, idx) => {
                                        const outputStyle = getOutputStatusStyles(out.status);
                                        const outputTitle = out.type || out.filename || "Output";
                                        return (
                                            <div key={idx} className="bg-[#111] border border-[#333] rounded-lg p-3 group hover:border-[#555] transition-colors">
                                                <div className="flex justify-between items-start mb-2">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-sm font-medium text-white">{outputTitle}</span>
                                                        <span className={`w-1.5 h-1.5 rounded-full ${outputStyle.dot}${outputStyle.pulse ? " animate-pulse" : ""}`}></span>
                                                    </div>
                                                    {out.status === "READY" && out.url && (
                                                        <button className="text-[#666] hover:text-white transition-colors" title="Download">
                                                            <Download className="w-4 h-4" />
                                                        </button>
                                                    )}
                                                </div>
                                                {out.filename && (
                                                    <p className="text-[10px] text-[#666] font-mono break-all mb-1">{out.filename}</p>
                                                )}
                                                <div className="flex justify-between items-center text-[10px]">
                                                    <span className={`font-bold ${outputStyle.text}`}>{out.status}</span>
                                                    {out.error_msg && <span className="text-red-400 truncate ml-2 max-w-[150px]">{out.error_msg}</span>}
                                                    {out.size && <span className="text-[#666]">{out.size}</span>}
                                                </div>
                                            </div>
                                        );
                                    })
                                ) : (
                                    <p className="text-xs text-[#555] italic">No outputs defined yet.</p>
                                )}
                            </div>

                            {/* Timeline / events could go here */}
                            <h3 className="text-xs font-bold text-[#666] uppercase tracking-wider mt-8 mb-4">Recent Activity</h3>
                            <div className="border-l border-[#333] pl-4 space-y-4">
                                <div className="relative">
                                    <div className="absolute -left-[21px] w-2.5 h-2.5 rounded-full bg-[#333] border border-[#1a1a1a]"></div>
                                    <p className="text-xs text-[#aaa]">Track Created</p>
                                    <p className="text-[10px] text-[#666]">2 hours ago</p>
                                </div>
                                {/* Mock history */}
                            </div>
                        </div>

                        {/* Footer Actions */}
                        <div className="p-4 border-t border-[#333] bg-[#222]">
                            <button className="w-full bg-[#333] hover:bg-[#444] text-white text-xs font-bold py-3 rounded-lg border border-[#444] transition-colors">
                                View Full Logs
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function getTrackStatusStyles(status: string) {
    switch (status) {
        case "APPROVED":
        case "COMPLETED":
            return { className: "bg-emerald-900/20 border-emerald-900/50 text-emerald-400" };
        case "REVIEWING":
            return { className: "bg-amber-900/20 border-amber-900/50 text-amber-400" };
        case "TRANSCRIBING":
            return { className: "bg-blue-900/20 border-blue-900/50 text-blue-400", pulse: true };
        case "TRANSCRIBED":
            return { className: "bg-blue-900/20 border-blue-900/50 text-blue-400" };
        case "TRANSLATING":
            return { className: "bg-purple-900/20 border-purple-900/50 text-purple-400", pulse: true };
        case "BURNING":
            return { className: "bg-orange-900/20 border-orange-900/50 text-orange-400", pulse: true };
        case "ERROR":
            return { className: "bg-red-900/20 border-red-900/50 text-red-400" };
        case "QUEUED":
        case "MISSING":
        default:
            return { className: "bg-[#222] border-[#333] text-[#888]" };
    }
}

function getOutputStatusStyles(status: string) {
    switch (status) {
        case "READY":
            return { text: "text-emerald-500", dot: "bg-emerald-500" };
        case "FAILED":
            return { text: "text-red-400", dot: "bg-red-500" };
        case "BURNING":
            return { text: "text-orange-400", dot: "bg-orange-500", pulse: true };
        case "QUEUED":
        case "PENDING":
        default:
            return { text: "text-[#888]", dot: "bg-[#555]" };
    }
}

// Subcomponent for Cells
function Cell({ track, onClick, isSelected }: { track?: Track; onClick: () => void; isSelected: boolean }) {
    if (!track || track.status === 'MISSING') {
        return (
            <div className="p-4 border-l border-[#333] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                <button onClick={onClick} className="text-[10px] font-bold text-[#444] hover:text-[#888] border border-[#333] hover:border-[#666] px-3 py-1.5 rounded-full transition-all">
                    + Create
                </button>
            </div>
        );
    }

    const trackStyle = getTrackStatusStyles(track.status);
    let baseClass = trackStyle.className;
    if (trackStyle.pulse) {
        baseClass += " animate-pulse";
    }

    // Selected State Override
    if (isSelected) {
        baseClass += " ring-1 ring-white/50";
    }

    // Decorators
    const showRedDot = track.has_error;
    const showGreenCheck = Boolean(track.outputs_total && track.outputs_ready === track.outputs_total);

    return (
        <div onClick={onClick} className={`p-4 border-l border-[#333] flex items-center justify-center relative cursor-pointer hover:bg-[#2a2a2a] transition-colors ${isSelected ? 'bg-[#2a2a2a]' : ''}`}>
            <div className={`px-3 py-1.5 rounded-full text-xs font-medium border ${baseClass} flex items-center gap-2 shadow-sm transition-all`}>
                {track.status_label || track.status}
            </div>

            {/* Decorators */}
            {showRedDot && (
                <div className="absolute top-3 right-3 w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]"></div>
            )}
            {!showRedDot && showGreenCheck && (
                <div className="absolute top-3 right-3 text-[10px] text-emerald-500">✓</div>
            )}
        </div>
    );
}
