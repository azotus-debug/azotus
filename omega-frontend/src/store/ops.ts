"use client";

import { create } from "zustand";
import { useToastStore } from "@/store/toast";
import { apiFetch, API_BASE } from "@/lib/api";

// Types for Operations Command Center

export interface OpsSummary {
  total_active: number;
  stage_counts: Record<string, number>;
  bottlenecks: Array<{ stage: string; count: number }>;
  urgent: number;
  overdue: number;
  ready_for_delivery: number;
  ready_for_burn: number;
  awaiting_review: number;
  failed: number;
  timestamp: string;
}

export interface QueueTrack {
  id: string;
  program_id: string;
  program_title: string;
  language_code: string;
  type: string;
  stage: string;
  status: string;
  due_date: string | null;
  client: string | null;
  urgency: "overdue" | "urgent" | "soon" | "normal";
  hours_until_due: number | null;
  output_path?: string;
  srt_path?: string;
  updated_at: string;
}

export interface PendingDelivery {
  id: string;
  program_id: string;
  program_title: string;
  language_code: string;
  type: string;
  stage: string;
  client: string | null;
  due_date: string | null;
  output_path?: string;
}

export interface DeliveryRecord {
  id: string;
  track_id: string;
  program_title: string;
  language_code: string;
  track_type: string;
  client: string | null;
  destination: string;
  delivered_at: string;
}

export interface BatchActionResult {
  track_id: string;
  status: "success" | "skipped" | "error";
  message: string;
}

export interface BatchActionResponse {
  action: string;
  total: number;
  success: number;
  skipped: number;
  errors: number;
  results: BatchActionResult[];
}

interface OpsStore {
  // Data
  summary: OpsSummary | null;
  queue: QueueTrack[];
  pendingDeliveries: PendingDelivery[];
  deliveryHistory: DeliveryRecord[];

  // Selection state
  selectedTrackIds: Set<string>;

  // Loading states
  loadingSummary: boolean;
  loadingQueue: boolean;
  loadingDeliveries: boolean;
  executingAction: boolean;

  // Error state
  error: string | null;

  // Last action result
  lastActionResult: BatchActionResponse | null;

  // Actions
  fetchSummary: () => Promise<void>;
  fetchQueue: (options?: { stage?: string; limit?: number }) => Promise<void>;
  fetchDeliveries: (options?: { days?: number }) => Promise<void>;

  // Selection
  toggleTrackSelection: (trackId: string) => void;
  selectAllTracks: () => void;
  clearSelection: () => void;
  selectByStage: (stage: string) => void;

  // Batch actions
  executeBatchAction: (action: string) => Promise<BatchActionResponse | null>;
  clearLastActionResult: () => void;

  // Refresh
  refreshAll: () => Promise<void>;

  // SSE
  handleSSEEvent: (eventType: string, data: unknown) => void;
}

// API_BASE imported from @/lib/api

export const useOpsStore = create<OpsStore>((set, get) => ({
  // Initial state
  summary: null,
  queue: [],
  pendingDeliveries: [],
  deliveryHistory: [],
  selectedTrackIds: new Set(),
  loadingSummary: false,
  loadingQueue: false,
  loadingDeliveries: false,
  executingAction: false,
  error: null,
  lastActionResult: null,

  fetchSummary: async () => {
    set({ loadingSummary: true, error: null });
    try {
      const res = await apiFetch(`${API_BASE}/api/v2/ops/summary`);
      if (!res.ok) throw new Error(`Failed to fetch summary: ${res.status}`);
      const summary = await res.json();
      set({ summary, loadingSummary: false });
    } catch (e) {
      set({ error: (e as Error).message, loadingSummary: false });
      useToastStore.getState().addToast("Failed to load ops summary", "error");
    }
  },

  fetchQueue: async (options = {}) => {
    set({ loadingQueue: true, error: null });
    try {
      const params = new URLSearchParams();
      if (options.stage) params.set("stage", options.stage);
      if (options.limit) params.set("limit", String(options.limit));

      const url = `${API_BASE}/api/v2/ops/queue${params.toString() ? `?${params}` : ""}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed to fetch queue: ${res.status}`);
      const data = await res.json();
      set({ queue: data.tracks || [], loadingQueue: false });
    } catch (e) {
      set({ error: (e as Error).message, loadingQueue: false });
      useToastStore.getState().addToast("Failed to load queue", "error");
    }
  },

  fetchDeliveries: async (options = {}) => {
    set({ loadingDeliveries: true, error: null });
    try {
      const params = new URLSearchParams();
      if (options.days) params.set("days", String(options.days));

      const url = `${API_BASE}/api/v2/ops/deliveries${params.toString() ? `?${params}` : ""}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed to fetch deliveries: ${res.status}`);
      const data = await res.json();
      set({
        pendingDeliveries: data.pending || [],
        deliveryHistory: data.delivered || [],
        loadingDeliveries: false,
      });
    } catch (e) {
      set({ error: (e as Error).message, loadingDeliveries: false });
      useToastStore.getState().addToast("Failed to load deliveries", "error");
    }
  },

  toggleTrackSelection: (trackId: string) => {
    const selected = new Set(get().selectedTrackIds);
    if (selected.has(trackId)) {
      selected.delete(trackId);
    } else {
      selected.add(trackId);
    }
    set({ selectedTrackIds: selected });
  },

  selectAllTracks: () => {
    const allIds = new Set(get().queue.map(t => t.id));
    set({ selectedTrackIds: allIds });
  },

  clearSelection: () => {
    set({ selectedTrackIds: new Set() });
  },

  selectByStage: (stage: string) => {
    const matchingIds = new Set(
      get().queue.filter(t => t.stage === stage).map(t => t.id)
    );
    set({ selectedTrackIds: matchingIds });
  },

  executeBatchAction: async (action: string) => {
    const { selectedTrackIds } = get();
    if (selectedTrackIds.size === 0) {
      set({ error: "No tracks selected" });
      return null;
    }

    set({ executingAction: true, error: null, lastActionResult: null });

    try {
      const res = await apiFetch(`${API_BASE}/api/v2/ops/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          track_ids: Array.from(selectedTrackIds),
          idempotency_key: `${action}-${Date.now()}`,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Action failed: ${res.status}`);
      }

      const result: BatchActionResponse = await res.json();
      set({ lastActionResult: result, executingAction: false });
      useToastStore.getState().addToast("Batch action completed", "success");

      // Refresh data after action
      get().refreshAll();

      // Clear selection if all succeeded
      if (result.errors === 0) {
        set({ selectedTrackIds: new Set() });
      }

      return result;
    } catch (e) {
      set({ error: (e as Error).message, executingAction: false });
      useToastStore.getState().addToast((e as Error).message, "error");
      return null;
    }
  },

  clearLastActionResult: () => {
    set({ lastActionResult: null });
  },

  refreshAll: async () => {
    const { fetchSummary, fetchQueue, fetchDeliveries } = get();
    await Promise.all([
      fetchSummary(),
      fetchQueue(),
      fetchDeliveries(),
    ]);
  },

  handleSSEEvent: (eventType: string, data: unknown) => {
    const { refreshAll } = get();

    switch (eventType) {
      case "ops_updated":
      case "programs_updated":
        refreshAll();
        break;
      case "track_progress": {
        // Optimistic in-place update of matching track in queue
        const payload = (typeof data === "object" && data !== null ? data : {}) as Record<string, unknown>;
        const track_id = typeof payload.track_id === "string" ? payload.track_id : "";
        const progress = typeof payload.progress === "number" ? payload.progress : undefined;
        const status = typeof payload.status === "string" ? payload.status : undefined;
        const stage = typeof payload.stage === "string" ? payload.stage : undefined;
        if (!track_id) break;
        set((state) => ({
          queue: state.queue.map((track) =>
            track.id === track_id
              ? {
                    ...track,
                    ...(progress !== undefined && { progress }),
                    ...(status !== undefined && { status }),
                    ...(stage !== undefined && { stage }),
                }
              : track
          ),
        }));
        break;
      }
    }
  },
}));
