"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { SubtitleEditor } from "@/components/SubtitleEditor";
import { Loader2, AlertTriangle, Lock } from "lucide-react";
import { apiFetch, setStoredToken } from "@/lib/api";
import { useRealtime } from "@/contexts/RealtimeContext";

type Segment = {
    id?: number;
    start: number;
    end: number;
    text: string;
    source_text?: string;
    [key: string]: unknown;
};

type GraphicZone = {
    id: string;
    startTime: number;
    endTime: number;
    label: string;
    position: "top" | "bottom";
    [key: string]: unknown;
};

type TrackInfo = {
    language_code?: string;
    meta?: {
        editor_report?: unknown;
        target_language?: string;
        [key: string]: unknown;
    };
    [key: string]: unknown;
};


export default function EditorPage() {
    const params = useParams();
    const searchParams = useSearchParams();
    const jobId = params?.jobId as string;
    const { socket } = useRealtime();

    // Review token from URL (shared review link)
    const reviewToken = searchParams?.get("review_token");
    const [isReviewMode, setIsReviewMode] = useState(false);

    const [segments, setSegments] = useState<Segment[]>([]);
    const [graphicZones, setGraphicZones] = useState<GraphicZone[]>([]);
    const [track, setTrack] = useState<TrackInfo | null>(null);
    const [bunnyDirectUrl, setBunnyDirectUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Lock state
    const [lockOwner, setLockOwner] = useState<string | null>(null);
    const [readOnly, setReadOnly] = useState(false);
    const lockAcquired = useRef(false);

    // If review_token provided, use it as auth token
    useEffect(() => {
        if (reviewToken) {
            setStoredToken(reviewToken);
            setIsReviewMode(true);
        }
    }, [reviewToken]);

    // Load editor data
    useEffect(() => {
        if (!jobId) return;

        const loadData = async () => {
            try {
                const res = await apiFetch(`/api/editor/${jobId}`);

                if (!res.ok) {
                    const errorText = await res.text();
                    throw new Error(`Failed to load job data: ${res.status} - ${errorText}`);
                }
                const data = await res.json();
                setSegments(data.segments || []);
                setGraphicZones(data.graphic_zones || []);
                setTrack(data.track || null);
                setBunnyDirectUrl(data.bunny_direct_url || null);
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                setError(message);
            } finally {
                setLoading(false);
            }
        };

        loadData();
    }, [jobId]);

    // Acquire lock on mount, release on unmount (skip for review mode)
    useEffect(() => {
        if (!jobId || loading || error || isReviewMode) return;

        const acquireLock = async () => {
            try {
                const res = await apiFetch(`/api/editor/${jobId}/lock`, { method: "POST" });
                if (res.ok) {
                    lockAcquired.current = true;
                    setReadOnly(false);
                    setLockOwner(null);
                } else if (res.status === 409) {
                    const data = await res.json();
                    const detail = data.detail || data;
                    setLockOwner(detail.locked_by || "another user");
                    setReadOnly(true);
                }
            } catch {
                // Lock acquisition failed — open read-only
                setReadOnly(true);
            }
        };

        acquireLock();

        // Tell the server which job we're editing (for disconnect cleanup)
        if (socket) {
            socket.emit("editing_job", { job_id: jobId });
        }

        return () => {
            if (lockAcquired.current) {
                // Fire-and-forget release on unmount
                apiFetch(`/api/editor/${jobId}/lock`, { method: "DELETE" }).catch(() => {});
                lockAcquired.current = false;
            }
        };
    }, [jobId, loading, error, socket, isReviewMode]);

    // Release lock on page unload/close
    useEffect(() => {
        if (!jobId) return;
        const handleBeforeUnload = () => {
            if (lockAcquired.current) {
                // Use fetch with keepalive for reliable delivery during page close
                const token = localStorage.getItem("omega_token");
                const headers: Record<string, string> = {};
                if (token) headers["Authorization"] = `Bearer ${token}`;
                fetch(`/api/editor/${jobId}/lock`, {
                    method: "DELETE",
                    headers,
                    keepalive: true,
                }).catch(() => {});
            }
        };
        window.addEventListener("beforeunload", handleBeforeUnload);
        return () => window.removeEventListener("beforeunload", handleBeforeUnload);
    }, [jobId]);

    // Listen for lock changes via Socket.IO
    useEffect(() => {
        if (!socket || !jobId) return;
        const handleLockChanged = (data: { job_id: string; locked_by: string | null }) => {
            if (data.job_id !== jobId) return;
            if (data.locked_by === null) {
                setLockOwner(null);
                // If we lost our lock, don't auto-reacquire
            } else {
                setLockOwner(data.locked_by);
            }
        };
        socket.on("lock_changed", handleLockChanged);
        return () => { socket.off("lock_changed", handleLockChanged); };
    }, [socket, jobId]);

    const handleForceUnlock = useCallback(async () => {
        if (!jobId) return;
        const res = await apiFetch(`/api/editor/${jobId}/lock`, { method: "DELETE" });
        if (res.ok) {
            // Now try to acquire
            const lockRes = await apiFetch(`/api/editor/${jobId}/lock`, { method: "POST" });
            if (lockRes.ok) {
                lockAcquired.current = true;
                setReadOnly(false);
                setLockOwner(null);
            }
        }
    }, [jobId]);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-screen bg-omega-base text-omega-text-primary">
                <Loader2 className="w-8 h-8 animate-spin text-omega-primary" />
                <span className="ml-3 font-mono text-sm">INITIALIZING WORKSTATION...</span>
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex items-center justify-center h-screen bg-omega-base text-omega-text-primary">
                <div className="flex flex-col items-center bg-omega-panel p-8 rounded-lg border border-omega-border">
                    <AlertTriangle className="w-12 h-12 text-omega-alert mb-4" />
                    <h2 className="text-xl font-bold">PROJECT LOAD FAILED</h2>
                    <p className="text-omega-text-secondary mt-2 font-mono text-sm">{error}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-screen">
            {readOnly && lockOwner && (
                <div className="flex items-center justify-between px-4 py-2 bg-amber-900/30 border-b border-amber-700/50 text-amber-200 text-sm">
                    <div className="flex items-center gap-2">
                        <Lock className="w-4 h-4" />
                        <span>Read-only — locked by <strong>{lockOwner}</strong></span>
                    </div>
                    <button
                        onClick={handleForceUnlock}
                        className="text-xs px-3 py-1 rounded bg-amber-700/50 hover:bg-amber-700/80 transition-colors"
                    >
                        Force Unlock
                    </button>
                </div>
            )}
            <SubtitleEditor
                jobId={jobId}
                initialSegments={segments}
                initialGraphicZones={graphicZones}
                track={track}
                bunnyDirectUrl={bunnyDirectUrl}
                readOnly={readOnly || isReviewMode}
                mode={isReviewMode ? "review" : "edit"}
            />
        </div>
    );
}
