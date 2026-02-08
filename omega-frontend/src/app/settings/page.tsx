"use client";

import { useState, useEffect, useCallback } from "react";
import {
    Settings as SettingsIcon,
    User,
    Globe,
    Palette,
    Cloud,
    Loader2,
    AlertCircle,
} from "lucide-react";
import { WorkspaceShell } from "@/components/layout/WorkspaceShell";
import { Sidebar, SidebarSection, SidebarItem } from "@/components/layout/Sidebar";
import { Inspector, InspectorSection } from "@/components/layout/Inspector";
import { useSettingsStore } from "@/store/settings";
import { useProgramsStore } from "@/store/programs";

type SettingsSection = "general" | "profiles" | "languages" | "styles" | "cloud";

// Shared select style
const selectStyle: React.CSSProperties = {
    padding: '8px 12px',
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 6,
    color: '#f5f5f5',
    fontSize: 13,
};

// Shared input style
const inputStyle: React.CSSProperties = {
    ...selectStyle,
    width: 240,
};

export default function SettingsWorkspace() {
    const [section, setSection] = useState<SettingsSection>("general");
    const [localEmail, setLocalEmail] = useState<string | null>(null);

    const { settings, loading, error, fetchSettings, updateSettings } = useSettingsStore();
    const { languages, voices, fetchLanguages, fetchVoices } = useProgramsStore();

    // Fetch settings + reference data on mount
    useEffect(() => {
        fetchSettings();
        fetchLanguages();
        fetchVoices();
    }, [fetchSettings, fetchLanguages, fetchVoices]);

    // Sync local email when settings load
    useEffect(() => {
        if (settings && localEmail === null) {
            setLocalEmail(String(settings.notification_email ?? ""));
        }
    }, [settings, localEmail]);

    // Helper to update a single setting (immediate for selects/toggles)
    const handleChange = useCallback(
        (key: string, value: string | boolean) => {
            updateSettings({ [key]: value });
        },
        [updateSettings],
    );

    // Toggle component wired to settings
    const Toggle = ({ settingKey }: { settingKey: string }) => {
        const checked = Boolean(settings?.[settingKey]);
        return (
            <div
                onClick={() => handleChange(settingKey, !checked)}
                style={{
                    width: 44,
                    height: 24,
                    background: checked ? 'rgba(82,139,255,0.5)' : 'rgba(255,255,255,0.12)',
                    borderRadius: 12,
                    position: 'relative',
                    cursor: 'pointer',
                    transition: 'background 0.2s',
                }}
            >
                <div style={{
                    width: 20,
                    height: 20,
                    background: '#fff',
                    borderRadius: '50%',
                    position: 'absolute',
                    top: 2,
                    transition: 'all 0.2s',
                    ...(checked ? { right: 2 } : { left: 2 }),
                }} />
            </div>
        );
    };

    // Sidebar
    const sidebarContent = (
        <Sidebar>
            <SidebarSection title="Settings">
                <SidebarItem
                    label="General"
                    isActive={section === "general"}
                    onClick={() => setSection("general")}
                    icon={<SettingsIcon style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Profiles"
                    isActive={section === "profiles"}
                    onClick={() => setSection("profiles")}
                    icon={<User style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Languages"
                    isActive={section === "languages"}
                    onClick={() => setSection("languages")}
                    icon={<Globe style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Styles"
                    isActive={section === "styles"}
                    onClick={() => setSection("styles")}
                    icon={<Palette style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Cloud"
                    isActive={section === "cloud"}
                    onClick={() => setSection("cloud")}
                    icon={<Cloud style={{ width: 14, height: 14 }} />}
                />
            </SidebarSection>
        </Sidebar>
    );

    // Inspector - shows help for current section
    const sectionHelp: Record<SettingsSection, { title: string; description: string }> = {
        general: { title: "General Settings", description: "Configure default behavior and paths." },
        profiles: { title: "Client Profiles", description: "Manage presets for different clients." },
        languages: { title: "Languages", description: "Configure target languages and translations." },
        styles: { title: "Subtitle Styles", description: "ASS template settings for different formats." },
        cloud: { title: "Cloud Pipeline", description: "Configure cloud translation and processing." },
    };

    const inspectorContent = (
        <Inspector title="Help">
            <InspectorSection title={sectionHelp[section].title}>
                <div style={{ padding: '8px 0', fontSize: 12, color: '#9ca3af', lineHeight: 1.6 }}>
                    {sectionHelp[section].description}
                </div>
            </InspectorSection>
        </Inspector>
    );

    // Settings Row Component
    const SettingRow = ({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) => (
        <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 0',
            borderBottom: '1px solid rgba(255,255,255,0.06)'
        }}>
            <div>
                <p style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>{label}</p>
                {hint && <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 4 }}>{hint}</p>}
            </div>
            <div>{value}</div>
        </div>
    );

    // Loading state
    if (loading && !settings) {
        return (
            <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
                <div style={{ padding: 24, display: 'flex', alignItems: 'center', gap: 8, color: '#9ca3af' }}>
                    <Loader2 style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} />
                    Loading settings...
                </div>
            </WorkspaceShell>
        );
    }

    // Error state (only if we have no settings at all)
    if (error && !settings) {
        return (
            <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
                <div style={{ padding: 24, display: 'flex', alignItems: 'center', gap: 8, color: '#ef4444' }}>
                    <AlertCircle style={{ width: 16, height: 16 }} />
                    {error}
                    <button
                        onClick={() => fetchSettings()}
                        style={{
                            marginLeft: 12,
                            padding: '4px 12px',
                            background: 'rgba(255,255,255,0.08)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            borderRadius: 6,
                            color: '#f5f5f5',
                            fontSize: 12,
                            cursor: 'pointer',
                        }}
                    >
                        Retry
                    </button>
                </div>
            </WorkspaceShell>
        );
    }

    // Render section content
    const renderSection = () => {
        switch (section) {
            case "general":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>General</h2>
                        <SettingRow
                            label="Default Target Language"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.default_target_language ?? "is"}
                                    onChange={(e) => handleChange("default_target_language", e.target.value)}
                                >
                                    {languages.length > 0
                                        ? languages.map((lang) => (
                                            <option key={lang.code} value={lang.code}>{lang.name}</option>
                                          ))
                                        : <>
                                            <option value="is">Icelandic</option>
                                            <option value="en">English</option>
                                            <option value="es">Spanish</option>
                                          </>
                                    }
                                </select>
                            }
                            hint="Language used for new jobs by default"
                        />
                        <SettingRow
                            label="Default Subtitle Style"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.default_subtitle_style ?? "Classic"}
                                    onChange={(e) => handleChange("default_subtitle_style", e.target.value)}
                                >
                                    <option value="Classic">Classic</option>
                                    <option value="Modern">Modern</option>
                                    <option value="Apple">Apple TV</option>
                                </select>
                            }
                            hint="ASS template for burned subtitles"
                        />
                        <SettingRow
                            label="Default Voice"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.default_voice ?? "alloy"}
                                    onChange={(e) => handleChange("default_voice", e.target.value)}
                                >
                                    {voices.length > 0
                                        ? voices.map((v) => (
                                            <option key={v.id} value={v.id}>{v.name}</option>
                                          ))
                                        : <option value="alloy">Alloy</option>
                                    }
                                </select>
                            }
                            hint="Default TTS voice for dubbing tracks"
                        />
                        <SettingRow
                            label="Default Delivery Profile"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.default_delivery_profile ?? "broadcast_hevc"}
                                    onChange={(e) => handleChange("default_delivery_profile", e.target.value)}
                                >
                                    <option value="broadcast_hevc">Broadcast HEVC (Fast)</option>
                                    <option value="broadcast_h264">Broadcast H.264 (Universal)</option>
                                    <option value="web">Web Optimized</option>
                                    <option value="archive">Archive (Master)</option>
                                    <option value="universal">Universal (Safe Default)</option>
                                </select>
                            }
                            hint="Encoding profile for video output"
                        />
                        <SettingRow
                            label="Auto-Burn on Finalize"
                            value={<Toggle settingKey="auto_burn_on_finalize" />}
                            hint="Automatically burn subtitles after finalization"
                        />
                    </div>
                );

            case "profiles":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Client Profiles</h2>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {["FBS", "Oral Roberts", "Times Square Church", "Default"].map((profile) => (
                                <div key={profile} style={{
                                    padding: 16,
                                    background: 'rgba(255,255,255,0.03)',
                                    border: '1px solid rgba(255,255,255,0.06)',
                                    borderRadius: 8,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between',
                                }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                        <div style={{
                                            width: 40,
                                            height: 40,
                                            background: 'rgba(82,139,255,0.15)',
                                            borderRadius: 8,
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                        }}>
                                            <User style={{ width: 18, height: 18, color: '#528BFF' }} />
                                        </div>
                                        <div>
                                            <p style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>{profile}</p>
                                            <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 2 }}>
                                                {settings?.default_target_language ?? "is"} &bull; {settings?.default_subtitle_style ?? "Classic"}
                                            </p>
                                        </div>
                                    </div>
                                    <button style={{
                                        padding: '6px 12px',
                                        background: 'rgba(255,255,255,0.05)',
                                        border: '1px solid rgba(255,255,255,0.1)',
                                        borderRadius: 6,
                                        color: '#9ca3af',
                                        fontSize: 12,
                                        cursor: 'pointer',
                                    }}>
                                        Edit
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                );

            case "languages":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Languages</h2>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {languages.length > 0
                                ? languages.map((lang) => (
                                    <div key={lang.code} style={{
                                        padding: 16,
                                        background: 'rgba(255,255,255,0.03)',
                                        border: '1px solid rgba(255,255,255,0.06)',
                                        borderRadius: 8,
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                    }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                            <div style={{
                                                width: 40,
                                                height: 40,
                                                background: 'rgba(82,139,255,0.15)',
                                                borderRadius: 8,
                                                display: 'flex',
                                                alignItems: 'center',
                                                justifyContent: 'center',
                                            }}>
                                                <Globe style={{ width: 18, height: 18, color: '#528BFF' }} />
                                            </div>
                                            <div>
                                                <p style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>{lang.name}</p>
                                                <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 2 }}>
                                                    Mode: {lang.default_mode ?? "sub"} &bull; Voice: {lang.default_voice ?? "alloy"}
                                                </p>
                                            </div>
                                        </div>
                                        <span style={{ fontSize: 12, color: '#6b7280', fontFamily: 'monospace' }}>{lang.code}</span>
                                    </div>
                                ))
                                : <p style={{ color: '#6b7280', fontSize: 13 }}>No languages loaded yet.</p>
                            }
                        </div>
                    </div>
                );

            case "styles":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Subtitle Styles</h2>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
                            {[
                                { name: "Classic", desc: "Traditional broadcast" },
                                { name: "Modern", desc: "Clean modern look" },
                                { name: "Apple", desc: "Apple TV style" },
                            ].map((style) => {
                                const isSelected = (settings?.default_subtitle_style ?? "Classic") === style.name;
                                return (
                                    <div
                                        key={style.name}
                                        onClick={() => handleChange("default_subtitle_style", style.name)}
                                        style={{
                                            padding: 20,
                                            background: isSelected ? 'rgba(82,139,255,0.08)' : 'rgba(255,255,255,0.03)',
                                            border: isSelected
                                                ? '1px solid rgba(82,139,255,0.4)'
                                                : '1px solid rgba(255,255,255,0.06)',
                                            borderRadius: 12,
                                            textAlign: 'center',
                                            cursor: 'pointer',
                                            transition: 'all 0.15s',
                                        }}
                                    >
                                        <div style={{
                                            width: '100%',
                                            height: 80,
                                            background: '#0a0a0c',
                                            borderRadius: 8,
                                            marginBottom: 16,
                                            display: 'flex',
                                            alignItems: 'flex-end',
                                            justifyContent: 'center',
                                            paddingBottom: 12,
                                        }}>
                                            <span style={{
                                                fontSize: 12,
                                                color: '#fff',
                                                padding: '4px 12px',
                                                background: style.name === "Classic" ? 'transparent' : 'rgba(0,0,0,0.7)',
                                                borderRadius: 4,
                                                textShadow: style.name === "Modern" ? '0 2px 4px rgba(0,0,0,0.5)' : 'none',
                                            }}>
                                                Sample subtitle
                                            </span>
                                        </div>
                                        <p style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>{style.name}</p>
                                        <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 4 }}>{style.desc}</p>
                                        {isSelected && (
                                            <p style={{ fontSize: 11, color: '#528BFF', margin: 0, marginTop: 6 }}>Selected</p>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                );

            case "cloud":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Cloud Pipeline</h2>
                        <SettingRow
                            label="Cloud Translation"
                            value={<Toggle settingKey="cloud_translation_enabled" />}
                            hint="Use Cloud Run for translation and editing"
                        />
                        <SettingRow
                            label="Region"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.cloud_region ?? "us-central1"}
                                    onChange={(e) => handleChange("cloud_region", e.target.value)}
                                >
                                    <option value="us-central1">us-central1</option>
                                    <option value="europe-west1">europe-west1</option>
                                </select>
                            }
                            hint="GCP region for cloud processing"
                        />
                        <SettingRow
                            label="Polish Mode"
                            value={
                                <select
                                    style={selectStyle}
                                    value={settings?.cloud_polish_mode ?? "review"}
                                    onChange={(e) => handleChange("cloud_polish_mode", e.target.value)}
                                >
                                    <option value="review">Review Only</option>
                                    <option value="all">All Jobs</option>
                                </select>
                            }
                            hint="When to apply polish pass"
                        />

                        <h3 style={{ fontSize: 15, fontWeight: 600, color: '#f5f5f5', marginTop: 32, marginBottom: 16 }}>Notifications</h3>
                        <SettingRow
                            label="Notification Email"
                            value={
                                <input
                                    type="email"
                                    style={inputStyle}
                                    placeholder="you@example.com"
                                    value={localEmail ?? ""}
                                    onChange={(e) => setLocalEmail(e.target.value)}
                                    onBlur={(e) => {
                                        const val = e.target.value;
                                        if (val !== String(settings?.notification_email ?? "")) {
                                            handleChange("notification_email", val);
                                        }
                                    }}
                                />
                            }
                            hint="Email address for pipeline notifications"
                        />
                        <SettingRow
                            label="Notify on Completion"
                            value={<Toggle settingKey="notification_on_complete" />}
                            hint="Send email when a job finishes successfully"
                        />
                        <SettingRow
                            label="Notify on Failure"
                            value={<Toggle settingKey="notification_on_failure" />}
                            hint="Send email when a job fails"
                        />
                    </div>
                );
        }
    };

    return (
        <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
            <div style={{ padding: 24, maxWidth: 800 }}>
                {renderSection()}
            </div>
        </WorkspaceShell>
    );
}
