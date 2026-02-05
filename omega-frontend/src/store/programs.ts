"use client";

import { create } from "zustand";

// Types matching API v2 responses
export interface Track {
    id: string;
    program_id: string;
    type: "subtitle" | "dub";
    language_code: string;
    language_name: string;
    stage: string;
    status: string;
    progress: number;
    rating?: number;
    voice_id?: string;
    job_id?: string;
    master_script_id?: string;
    output_path?: string;
    output_version?: number;
    output_override?: boolean;
    override_reason?: string;
    pending_resync?: boolean;
    locked_at?: string;
    locked_by?: string;
    srt_path?: string;
    video_path?: string;
    files_ready?: boolean;
    created_at: string;
    updated_at: string;
}

export interface Program {
    id: string;
    title: string;
    original_filename?: string;
    video_path?: string;
    thumbnail_path?: string;
    duration_seconds?: number;
    client?: string;
    due_date?: string;
    default_style?: string;
    created_at: string;
    updated_at: string;
    tracks: Track[];
    track_completion: string;
    needs_attention: boolean;
}

export interface Delivery {
    id: string;
    track_id: string;
    destination: string;
    recipient?: string;
    delivered_at: string;
    notes?: string;
    program_title?: string;
    language_code?: string;
    track_type?: string;
}

export interface LanguageOption {
    code: string;
    name: string;
    default_mode?: "dub" | "sub";
    default_voice?: string;
}

export interface VoiceOption {
    id: string;
    name: string;
    description?: string;
}

export interface PipelineStatsStage {
    stage: string;
    count: number;
}

export interface PipelineStats {
    total_active: number;
    blocked: number;
    needs_attention: number;
    failed?: number;
    stages?: Record<string, number> | PipelineStatsStage[];
}

export interface AddTrackResult {
    ok: boolean;
    trackId?: string;
    stage?: string;
    error?: string;
}

interface ProgramsStore {
    programs: Program[];
    activeTracks: Track[];
    deliveries: Delivery[];
    languages: LanguageOption[];
    voices: VoiceOption[];
    pipelineStats: PipelineStats | null;
    loading: boolean;
    error: string | null;

    // Actions
    fetchPrograms: () => Promise<void>;
    fetchActiveTracks: () => Promise<void>;
    fetchDeliveries: (days?: number) => Promise<void>;
    fetchPipelineStats: () => Promise<void>;
    fetchLanguages: () => Promise<void>;
    fetchVoices: () => Promise<void>;
    getProgram: (id: string) => Program | undefined;
    addTrack: (programId: string, type: string, languageCode: string, voiceId?: string) => Promise<AddTrackResult>;
    startDubbing: (trackId: string) => Promise<boolean>;
    recordDelivery: (trackId: string, destination: string, recipient?: string, notes?: string, notify?: { notifyEmail?: string; deliveryLink?: string; programTitle?: string; provisional?: boolean }) => Promise<boolean>;
    sendTrackToReview: (trackId: string) => Promise<boolean>;
    approveTrack: (trackId: string) => Promise<boolean>;
    revealFile: (trackId: string, fileType: 'video' | 'srt') => Promise<boolean>;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export const useProgramsStore = create<ProgramsStore>((set, get) => ({
    programs: [],
    activeTracks: [],
    deliveries: [],
    languages: [],
    voices: [],
    pipelineStats: null,
    loading: false,
    error: null,

    fetchPrograms: async () => {
        set({ loading: true, error: null });
        try {
            const res = await fetch(`${API_BASE}/api/v2/programs`);
            if (!res.ok) throw new Error(`Failed to fetch programs: ${res.status}`);
            const programs = await res.json();
            set({ programs, loading: false });
        } catch (e) {
            set({ error: (e as Error).message, loading: false });
        }
    },

    fetchActiveTracks: async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks/active`);
            if (!res.ok) throw new Error(`Failed to fetch active tracks: ${res.status}`);
            const activeTracks = await res.json();
            set({ activeTracks });
        } catch (e) {
            console.error("Failed to fetch active tracks:", e);
        }
    },

    fetchDeliveries: async (days = 7) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/deliveries?days=${days}`);
            if (!res.ok) throw new Error(`Failed to fetch deliveries: ${res.status}`);
            const deliveries = await res.json();
            set({ deliveries });
        } catch (e) {
            console.error("Failed to fetch deliveries:", e);
        }
    },

    fetchPipelineStats: async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/pipeline/stats`);
            if (!res.ok) throw new Error(`Failed to fetch pipeline stats: ${res.status}`);
            const pipelineStats = await res.json();
            set({ pipelineStats });
        } catch (e) {
            console.error("Failed to fetch pipeline stats:", e);
        }
    },

    fetchLanguages: async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/languages`);
            if (!res.ok) throw new Error(`Failed to fetch languages: ${res.status}`);
            const data = await res.json();
            set({ languages: data.languages || [] });
        } catch (e) {
            console.error("Failed to fetch languages:", e);
        }
    },

    fetchVoices: async () => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/voices`);
            if (!res.ok) throw new Error(`Failed to fetch voices: ${res.status}`);
            const data = await res.json();
            set({ voices: data.voices || [] });
        } catch (e) {
            console.error("Failed to fetch voices:", e);
        }
    },

    getProgram: (id: string) => {
        return get().programs.find((p) => p.id === id);
    },

    addTrack: async (programId, type, languageCode, voiceId) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/programs/${programId}/tracks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ type, language_code: languageCode, voice_id: voiceId }),
            });
            const data = await res.json();
            if (!res.ok) {
                return { ok: false, error: data?.error || "Failed to add track" };
            }
            // Refresh programs to get updated tracks
            await get().fetchPrograms();
            return { ok: true, trackId: data.track_id, stage: data.stage };
        } catch (e) {
            console.error("Failed to add track:", e);
            return { ok: false, error: (e as Error).message || "Failed to add track" };
        }
    },

    startDubbing: async (trackId) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks/${trackId}/start-dub`, {
                method: "POST",
            });
            if (!res.ok) throw new Error("Failed to start dubbing");
            await get().fetchPrograms();
            return true;
        } catch (e) {
            console.error("Failed to start dubbing:", e);
            return false;
        }
    },

    recordDelivery: async (trackId, destination, recipient, notes, notify) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/deliveries`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ track_id: trackId, method: destination, recipient, notes, ...notify }),
            });
            if (!res.ok) throw new Error("Failed to record delivery");
            // Refresh data
            await get().fetchPrograms();
            await get().fetchDeliveries();
            return true;
        } catch (e) {
            console.error("Failed to record delivery:", e);
            return false;
        }
    },

    sendTrackToReview: async (trackId) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks/${trackId}/send-to-review`, {
                method: "POST",
            });
            if (!res.ok) throw new Error("Failed to send track to review");
            await get().fetchPrograms();
            await get().fetchActiveTracks();
            return true;
        } catch (e) {
            console.error("Failed to send track to review:", e);
            return false;
        }
    },

    approveTrack: async (trackId) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks/${trackId}/approve`, {
                method: "POST",
            });
            if (!res.ok) throw new Error("Failed to approve track");
            await get().fetchPrograms();
            await get().fetchActiveTracks();
            return true;
        } catch (e) {
            console.error("Failed to approve track:", e);
            return false;
        }
    },

    revealFile: async (trackId, fileType) => {
        try {
            const res = await fetch(`${API_BASE}/api/v2/tracks/${trackId}/reveal`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ type: fileType }),
            });
            if (!res.ok) throw new Error("Failed to reveal file");
            return true;
        } catch (e) {
            console.error("Failed to reveal file:", e);
            return false;
        }
    },
}));
