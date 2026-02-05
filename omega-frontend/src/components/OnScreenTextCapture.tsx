"use client";

import { useState, useRef, useCallback } from "react";
import { Camera, Copy, Check, Type, X, Plus } from "lucide-react";

// =============================================================================
// OnScreenTextCapture Component
// =============================================================================

interface CapturedText {
    id: string;
    timestamp: number;
    text: string;
    imageUrl?: string;
    capturedAt: Date;
}

interface OnScreenTextCaptureProps {
    videoRef: React.RefObject<HTMLVideoElement | null>;
    onCreateSegment?: (timestamp: number, duration: number, text: string) => void;
}

export function OnScreenTextCapture({ videoRef, onCreateSegment }: OnScreenTextCaptureProps) {
    const [captures, setCaptures] = useState<CapturedText[]>([]);
    const [isCapturing, setIsCapturing] = useState(false);
    const [copiedId, setCopiedId] = useState<string | null>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);

    const captureFrame = useCallback(async () => {
        const video = videoRef.current;
        const canvas = canvasRef.current;

        if (!video || !canvas) return;

        setIsCapturing(true);

        try {
            // Draw video frame to canvas
            const ctx = canvas.getContext("2d");
            if (!ctx) return;

            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0);

            // Convert to image URL for display
            const imageUrl = canvas.toDataURL("image/png");

            // Create capture entry
            const capture: CapturedText = {
                id: `capture-${Date.now()}`,
                timestamp: video.currentTime,
                text: "", // User can manually enter the text they see
                imageUrl,
                capturedAt: new Date(),
            };

            setCaptures((prev) => [capture, ...prev.slice(0, 9)]); // Keep last 10
        } catch (e) {
            console.error("Failed to capture frame:", e);
        } finally {
            setIsCapturing(false);
        }
    }, [videoRef]);

    const updateCaptureText = useCallback((id: string, text: string) => {
        setCaptures((prev) =>
            prev.map((c) => (c.id === id ? { ...c, text } : c))
        );
    }, []);

    const copyText = useCallback((id: string, text: string) => {
        navigator.clipboard.writeText(text);
        setCopiedId(id);
        setTimeout(() => setCopiedId(null), 2000);
    }, []);

    const deleteCapture = useCallback((id: string) => {
        setCaptures((prev) => prev.filter((c) => c.id !== id));
    }, []);

    const formatTimestamp = (seconds: number): string => {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, "0")}`;
    };

    return (
        <div style={{ padding: 16, background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
            {/* Hidden canvas for capturing */}
            <canvas ref={canvasRef} style={{ display: "none" }} />

            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Type style={{ width: 14, height: 14, color: "#9ca3af" }} />
                <span style={{ flex: 1, fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.03em", color: "#f5f5f5" }}>
                    On-Screen Text
                </span>
            </div>

            {/* Capture Button */}
            <button
                onClick={captureFrame}
                disabled={isCapturing}
                style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    padding: "10px 16px",
                    fontSize: 12,
                    fontWeight: 500,
                    color: "#f5f5f5",
                    background: "rgba(82,139,255,0.15)",
                    border: "1px solid rgba(82,139,255,0.3)",
                    borderRadius: 6,
                    cursor: isCapturing ? "wait" : "pointer",
                    marginBottom: 12,
                }}
            >
                <Camera style={{ width: 14, height: 14 }} />
                {isCapturing ? "Capturing..." : "Capture Current Frame"}
            </button>

            {/* Captures List */}
            {captures.length === 0 ? (
                <div style={{ fontSize: 11, color: "#6b7280", fontStyle: "italic", textAlign: "center", padding: 12 }}>
                    Capture video frames to record on-screen text (titles, names, graphics)
                </div>
            ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {captures.map((capture) => (
                        <div
                            key={capture.id}
                            style={{
                                padding: 10,
                                background: "rgba(255,255,255,0.02)",
                                borderRadius: 6,
                                border: "1px solid rgba(255,255,255,0.05)",
                            }}
                        >
                            {/* Thumbnail + Timestamp */}
                            <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                                {capture.imageUrl && (
                                    <img
                                        src={capture.imageUrl}
                                        alt={`Capture at ${formatTimestamp(capture.timestamp)}`}
                                        style={{
                                            width: 60,
                                            height: 34,
                                            objectFit: "cover",
                                            borderRadius: 4,
                                            border: "1px solid rgba(255,255,255,0.1)",
                                        }}
                                    />
                                )}
                                <div style={{ flex: 1 }}>
                                    <div style={{ fontSize: 10, color: "#6b7280", marginBottom: 2 }}>
                                        @ {formatTimestamp(capture.timestamp)}
                                    </div>
                                    <input
                                        type="text"
                                        value={capture.text}
                                        onChange={(e) => updateCaptureText(capture.id, e.target.value)}
                                        placeholder="Enter text from frame..."
                                        style={{
                                            width: "100%",
                                            padding: "4px 8px",
                                            fontSize: 11,
                                            color: "#f5f5f5",
                                            background: "rgba(0,0,0,0.3)",
                                            border: "1px solid rgba(255,255,255,0.1)",
                                            borderRadius: 4,
                                            outline: "none",
                                        }}
                                    />
                                </div>
                            </div>

                            {/* Actions */}
                            <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                                {capture.text && (
                                    <>
                                        <button
                                            onClick={() => copyText(capture.id, capture.text)}
                                            style={{
                                                display: "flex",
                                                alignItems: "center",
                                                gap: 4,
                                                padding: "4px 8px",
                                                fontSize: 10,
                                                color: copiedId === capture.id ? "#22c55e" : "#9ca3af",
                                                background: "transparent",
                                                border: "1px solid rgba(255,255,255,0.1)",
                                                borderRadius: 4,
                                                cursor: "pointer",
                                            }}
                                        >
                                            {copiedId === capture.id ? <Check style={{ width: 10, height: 10 }} /> : <Copy style={{ width: 10, height: 10 }} />}
                                            {copiedId === capture.id ? "Copied" : "Copy"}
                                        </button>
                                        {onCreateSegment && (
                                            <button
                                                onClick={() => {
                                                    onCreateSegment(capture.timestamp, 4, capture.text);
                                                    deleteCapture(capture.id);
                                                }}
                                                style={{
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: 4,
                                                    padding: "4px 8px",
                                                    fontSize: 10,
                                                    fontWeight: 500,
                                                    color: "#22c55e",
                                                    background: "rgba(34,197,94,0.15)",
                                                    border: "1px solid rgba(34,197,94,0.3)",
                                                    borderRadius: 4,
                                                    cursor: "pointer",
                                                }}
                                            >
                                                <Plus style={{ width: 10, height: 10 }} />
                                                Create Segment
                                            </button>
                                        )}
                                    </>
                                )}
                                <button
                                    onClick={() => deleteCapture(capture.id)}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        padding: "4px 6px",
                                        fontSize: 10,
                                        color: "#6b7280",
                                        background: "transparent",
                                        border: "1px solid rgba(255,255,255,0.05)",
                                        borderRadius: 4,
                                        cursor: "pointer",
                                    }}
                                >
                                    <X style={{ width: 10, height: 10 }} />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
