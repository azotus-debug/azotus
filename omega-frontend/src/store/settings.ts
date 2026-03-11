"use client";

import { create } from "zustand";
import { useToastStore } from "@/store/toast";
import { apiFetch, API_BASE } from "@/lib/api";

export interface AppSettings {
  default_target_language: string;
  default_subtitle_style: string;
  auto_burn_on_finalize: boolean;
  cloud_translation_enabled: boolean;
  cloud_region: string;
  cloud_polish_mode: string;
  notification_email: string;
  notification_on_complete: boolean;
  notification_on_failure: boolean;
  default_voice: string;
  default_delivery_profile: string;
  [key: string]: string | boolean | number; // allow extra keys
}

interface SettingsStore {
  settings: AppSettings | null;
  loading: boolean;
  error: string | null;
  fetchSettings: () => Promise<void>;
  updateSettings: (partial: Partial<AppSettings>) => Promise<void>;
}

// API_BASE imported from @/lib/api

export const useSettingsStore = create<SettingsStore>((set) => ({
  settings: null,
  loading: false,
  error: null,

  fetchSettings: async () => {
    set({ loading: true, error: null });
    try {
      const res = await apiFetch(`${API_BASE}/api/v2/settings`);
      if (!res.ok) throw new Error(`Failed to fetch settings: ${res.status}`);
      const settings: AppSettings = await res.json();
      set({ settings, loading: false });
    } catch (e) {
      const message = (e as Error).message;
      set({ error: message, loading: false });
    }
  },

  updateSettings: async (partial: Partial<AppSettings>) => {
    const { addToast } = useToastStore.getState();
    set({ error: null });
    try {
      const res = await apiFetch(`${API_BASE}/api/v2/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partial),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Failed to save settings: ${res.status}`);
      }
      const settings: AppSettings = await res.json();
      set({ settings });
      addToast("Settings saved", "success");
    } catch (e) {
      const message = (e as Error).message;
      set({ error: message });
      addToast(message, "error");
    }
  },
}));
