"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { useSettingsStore } from "@/store/settings";
import { useProgramsStore } from "@/store/programs";

const STYLE_OPTIONS = [
  { value: "RUV_BOX", label: "RUV Box (Default)" },
  { value: "Classic", label: "Classic" },
  { value: "Apple", label: "Apple TV" },
  { value: "Modern", label: "Modern" },
];

interface SettingRowProps {
  label: string;
  hint: string;
  control: ReactNode;
}

function SettingRow({ label, hint, control }: SettingRowProps) {
  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 0",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <div style={{ minWidth: 220 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "#f5f5f5" }}>{label}</div>
        <div style={{ fontSize: 12, color: "#9ca3af", marginTop: 4 }}>{hint}</div>
      </div>
      <div>{control}</div>
    </div>
  );
}

const controlStyle: React.CSSProperties = {
  minWidth: 260,
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.12)",
  background: "rgba(255,255,255,0.04)",
  color: "#f5f5f5",
  fontSize: 13,
};

export default function SettingsView() {
  const { settings, loading, error, fetchSettings, updateSettings } = useSettingsStore();
  const { languages, voices, fetchLanguages, fetchVoices } = useProgramsStore();

  useEffect(() => {
    fetchSettings();
    fetchLanguages();
    fetchVoices();
  }, [fetchSettings, fetchLanguages, fetchVoices]);

  const values = useMemo(() => {
    const base = (settings || {}) as Record<string, unknown>;
    return {
      default_target_language: String(base["default_target_language"] || "is"),
      default_subtitle_style: String(base["default_subtitle_style"] || "RUV_BOX"),
      default_voice: String(base["default_voice"] || "alloy"),
      default_delivery_profile: String(base["default_delivery_profile"] || "broadcast_hevc"),
      auto_burn_on_finalize: Boolean(base["auto_burn_on_finalize"]),
      cloud_translation_enabled: Boolean(base["cloud_translation_enabled"]),
      notification_on_complete: Boolean(base["notification_on_complete"]),
      notification_on_failure: Boolean(base["notification_on_failure"]),
      notification_email: String(base["notification_email"] || ""),
      cloud_region: String(base["cloud_region"] || "europe-west1"),
      cloud_polish_mode: String(base["cloud_polish_mode"] || "balanced"),
    };
  }, [settings]);

  const onToggle = (key: string, current: boolean) => {
    void updateSettings({ [key]: !current });
  };

  if (loading && !settings) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#9ca3af" }}>
        <Loader2 size={16} className="animate-spin" />
        Loading settings...
      </div>
    );
  }

  if (error && !settings) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#ef4444" }}>
        <AlertCircle size={16} />
        {error}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6" style={{ animation: "fadeIn 0.25s ease" }}>
      <div className="page-header">
        <div>
          <h2 className="page-title">Settings</h2>
          <p className="page-subtitle">System defaults for ingest, translation, and delivery</p>
        </div>
      </div>

      <div
        style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 12,
          padding: "8px 18px",
        }}
      >
        <SettingRow
          label="Default Target Language"
          hint="Primary subtitle language for new jobs"
          control={
            <select
              style={controlStyle}
              value={values.default_target_language}
              onChange={(e) => void updateSettings({ default_target_language: e.target.value })}
            >
              {languages.length > 0
                ? languages.map((lang) => (
                    <option key={lang.code} value={lang.code}>
                      {lang.name}
                    </option>
                  ))
                : [
                    <option key="is" value="is">Icelandic</option>,
                    <option key="en" value="en">English</option>,
                    <option key="es" value="es">Spanish</option>,
                  ]}
            </select>
          }
        />

        <SettingRow
          label="Default Subtitle Style"
          hint="Burn style used by automatic pipelines"
          control={
            <select
              style={controlStyle}
              value={values.default_subtitle_style}
              onChange={(e) => void updateSettings({ default_subtitle_style: e.target.value })}
            >
              {STYLE_OPTIONS.map((style) => (
                <option key={style.value} value={style.value}>
                  {style.label}
                </option>
              ))}
            </select>
          }
        />

        <SettingRow
          label="Default Voice"
          hint="Used for dub tracks unless overridden"
          control={
            <select
              style={controlStyle}
              value={values.default_voice}
              onChange={(e) => void updateSettings({ default_voice: e.target.value })}
            >
              {voices.length > 0
                ? voices.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.name}
                    </option>
                  ))
                : [<option key="alloy" value="alloy">Alloy</option>]}
            </select>
          }
        />

        <SettingRow
          label="Default Delivery Profile"
          hint="Encoding profile for final exported video"
          control={
            <select
              style={controlStyle}
              value={values.default_delivery_profile}
              onChange={(e) => void updateSettings({ default_delivery_profile: e.target.value })}
            >
              <option value="broadcast_hevc">Broadcast HEVC</option>
              <option value="broadcast_h264">Broadcast H.264</option>
              <option value="web">Web</option>
              <option value="archive">Archive</option>
              <option value="universal">Universal</option>
            </select>
          }
        />

        <SettingRow
          label="Cloud Region"
          hint="Region for cloud translation workers"
          control={
            <input
              style={controlStyle}
              value={values.cloud_region}
              onChange={(e) => void updateSettings({ cloud_region: e.target.value })}
            />
          }
        />

        <SettingRow
          label="Cloud Polish Mode"
          hint="Post-translation quality profile"
          control={
            <select
              style={controlStyle}
              value={values.cloud_polish_mode}
              onChange={(e) => void updateSettings({ cloud_polish_mode: e.target.value })}
            >
              <option value="fast">Fast</option>
              <option value="balanced">Balanced</option>
              <option value="strict">Strict</option>
            </select>
          }
        />

        <SettingRow
          label="Notification Email"
          hint="Receives completion/failure alerts"
          control={
            <input
              style={controlStyle}
              value={values.notification_email}
              onChange={(e) => void updateSettings({ notification_email: e.target.value })}
              placeholder="name@example.com"
            />
          }
        />

        <SettingRow
          label="Auto Burn on Finalize"
          hint="Automatically queue burn after subtitle finalization"
          control={
            <button
              type="button"
              onClick={() => onToggle("auto_burn_on_finalize", values.auto_burn_on_finalize)}
              style={{
                ...controlStyle,
                minWidth: 120,
                cursor: "pointer",
                background: values.auto_burn_on_finalize ? "rgba(34,197,94,0.18)" : "rgba(255,255,255,0.04)",
                borderColor: values.auto_burn_on_finalize ? "rgba(34,197,94,0.5)" : "rgba(255,255,255,0.12)",
              }}
            >
              {values.auto_burn_on_finalize ? "Enabled" : "Disabled"}
            </button>
          }
        />

        <SettingRow
          label="Cloud Translation"
          hint="Enable cloud path for translation stages"
          control={
            <button
              type="button"
              onClick={() => onToggle("cloud_translation_enabled", values.cloud_translation_enabled)}
              style={{
                ...controlStyle,
                minWidth: 120,
                cursor: "pointer",
                background: values.cloud_translation_enabled ? "rgba(34,197,94,0.18)" : "rgba(255,255,255,0.04)",
                borderColor: values.cloud_translation_enabled ? "rgba(34,197,94,0.5)" : "rgba(255,255,255,0.12)",
              }}
            >
              {values.cloud_translation_enabled ? "Enabled" : "Disabled"}
            </button>
          }
        />

        <SettingRow
          label="Notify on Complete"
          hint="Send notification when a track reaches done"
          control={
            <button
              type="button"
              onClick={() => onToggle("notification_on_complete", values.notification_on_complete)}
              style={{
                ...controlStyle,
                minWidth: 120,
                cursor: "pointer",
                background: values.notification_on_complete ? "rgba(34,197,94,0.18)" : "rgba(255,255,255,0.04)",
                borderColor: values.notification_on_complete ? "rgba(34,197,94,0.5)" : "rgba(255,255,255,0.12)",
              }}
            >
              {values.notification_on_complete ? "Enabled" : "Disabled"}
            </button>
          }
        />

        <SettingRow
          label="Notify on Failure"
          hint="Send notification when a track fails"
          control={
            <button
              type="button"
              onClick={() => onToggle("notification_on_failure", values.notification_on_failure)}
              style={{
                ...controlStyle,
                minWidth: 120,
                cursor: "pointer",
                background: values.notification_on_failure ? "rgba(239,68,68,0.18)" : "rgba(255,255,255,0.04)",
                borderColor: values.notification_on_failure ? "rgba(239,68,68,0.5)" : "rgba(255,255,255,0.12)",
              }}
            >
              {values.notification_on_failure ? "Enabled" : "Disabled"}
            </button>
          }
        />
      </div>

      {error && settings && (
        <div style={{ color: "#ef4444", fontSize: 12 }}>
          Last save error: {error}
        </div>
      )}
    </div>
  );
}
