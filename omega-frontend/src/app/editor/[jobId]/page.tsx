"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { SubtitleEditor } from "@/components/SubtitleEditor";
import { Loader2, AlertTriangle } from "lucide-react";

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
    const jobId = params?.jobId as string; // Next.js 13+ params are objects

    const [segments, setSegments] = useState<Segment[]>([]);
    const [graphicZones, setGraphicZones] = useState<GraphicZone[]>([]);
    const [track, setTrack] = useState<TrackInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!jobId) return;

        const loadData = async () => {
            try {
                const res = await fetch(`/api/editor/${jobId}`, {
                    credentials: 'include' // Include cookies for authentication
                });

                if (!res.ok) {
                    const errorText = await res.text();
                    throw new Error(`Failed to load job data: ${res.status} - ${errorText}`);
                }
                const data = await res.json();
                setSegments(data.segments || []);
                setGraphicZones(data.graphic_zones || []);
                setTrack(data.track || null);
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                setError(message);
            } finally {
                setLoading(false);
            }
        };

        loadData();
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

    return <SubtitleEditor jobId={jobId} initialSegments={segments} initialGraphicZones={graphicZones} track={track} />;
}
