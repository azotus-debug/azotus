"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRealtime } from "@/contexts/RealtimeContext";
import ProgressBar from "@/components/common/ProgressBar";
import { apiFetch, API_BASE } from "@/lib/api";
import { ChevronDown } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CloudTrack {
  track_id: string;
  program_id: string;
  program_title: string;
  language_code: string;
  language_name: string;
  stage: string;
  status: string;
  cloud_status: string;
  progress: number;
  elapsed_seconds: number | null;
  timeout_seconds: number;
  remaining_seconds: number | null;
  retry_count: number;
  cloud_started_at: string | null;
  updated_at: string | null;
}

interface CloudStatus {
  cloud_tracks: CloudTrack[];
  total_in_cloud: number;
  deadman_minutes: number;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatElapsed(seconds: number | null): string {
  if (seconds === null || seconds < 0) return "--:--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${String(rm).padStart(2, "0")}m`;
  }
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function formatRemaining(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds <= 0) return "Timed out";
  const m = Math.floor(seconds / 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m remaining`;
  }
  return `${m}m remaining`;
}

function timeoutClass(remaining: number | null, timeout: number): string {
  if (remaining === null || timeout <= 0) return "cloud-timeout--safe";
  const ratio = remaining / timeout;
  if (ratio > 0.5) return "cloud-timeout--safe";
  if (ratio > 0.25) return "cloud-timeout--warning";
  return "cloud-timeout--danger";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CloudWorkersPanel() {
  const [data, setData] = useState<CloudStatus | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [, setTick] = useState(0);
  const lastFetchRef = useRef<number>(0);
  const { socket } = useRealtime();

  // Fetch cloud status
  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_BASE}/api/v2/cloud/status`);
      if (!res.ok) return;
      const json: CloudStatus = await res.json();
      setData(json);
      lastFetchRef.current = Date.now();
    } catch {
      // Silently fail — panel will show stale data or nothing
    }
  }, []);

  // Initial fetch + 15s polling
  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 15_000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  // Tick elapsed time locally every second
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  // WebSocket subscription — refresh on cloud-related events
  useEffect(() => {
    if (!socket) return;
    const handler = () => fetchStatus();
    socket.on("track_updated", handler);
    return () => { socket.off("track_updated", handler); };
  }, [socket, fetchStatus]);

  // Don't render if no cloud jobs
  if (!data || data.total_in_cloud === 0) return null;

  // Adjust elapsed/remaining based on local ticking
  const secondsSinceFetch = Math.max(0, (Date.now() - lastFetchRef.current) / 1000);

  return (
    <div className="cloud-workers-panel">
      <div
        className="cloud-workers-header"
        onClick={() => setCollapsed((c) => !c)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setCollapsed((c) => !c);
        }}
      >
        <div className="cloud-workers-title">
          <span>Cloud Workers</span>
          <span className="cloud-workers-count">{data.total_in_cloud}</span>
        </div>
        <ChevronDown
          size={16}
          className={`cloud-workers-collapse ${collapsed ? "" : "cloud-workers-collapse--open"}`}
        />
      </div>

      {!collapsed && (
        <div className="cloud-workers-body">
          {data.cloud_tracks.map((track) => {
            const adjustedElapsed =
              track.elapsed_seconds !== null
                ? track.elapsed_seconds + secondsSinceFetch
                : null;
            const adjustedRemaining =
              track.remaining_seconds !== null
                ? Math.max(0, track.remaining_seconds - secondsSinceFetch)
                : null;

            return (
              <div key={track.track_id} className="cloud-track-row">
                {/* Top row: title, language, elapsed, retry */}
                <div className="cloud-track-top">
                  <div className="cloud-track-info">
                    <span className="cloud-track-title">
                      {track.program_title || track.track_id.slice(0, 12)}
                    </span>
                    <span className="cloud-track-lang">
                      {(track.language_code || "").toUpperCase()} Sub
                    </span>
                  </div>
                  <div className="cloud-track-timing">
                    {track.retry_count > 0 && (
                      <span className="cloud-retry-badge">
                        Retry #{track.retry_count}
                      </span>
                    )}
                    <span className="cloud-elapsed">
                      {formatElapsed(adjustedElapsed)}
                    </span>
                  </div>
                </div>

                {/* Middle row: progress bar */}
                <div className="cloud-track-progress">
                  <ProgressBar
                    value={track.progress}
                    label={`${track.program_title} cloud progress`}
                  />
                  <span className="cloud-progress-pct">
                    {Math.round(track.progress)}%
                  </span>
                </div>

                {/* Bottom row: full status text + timeout */}
                <div className="cloud-track-bottom">
                  <span className="cloud-status-text">
                    {track.cloud_status || track.status || "Working..."}
                  </span>
                  {adjustedRemaining !== null && track.timeout_seconds > 0 && (
                    <span
                      className={`cloud-timeout ${timeoutClass(adjustedRemaining, track.timeout_seconds)}`}
                    >
                      {formatRemaining(adjustedRemaining)}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
