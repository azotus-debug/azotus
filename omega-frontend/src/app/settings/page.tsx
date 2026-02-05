"use client";

import { useState } from "react";
import {
    Settings as SettingsIcon,
    User,
    Globe,
    Palette,
    Cloud,
} from "lucide-react";
import { WorkspaceShell } from "@/components/layout/WorkspaceShell";
import { Sidebar, SidebarSection, SidebarItem } from "@/components/layout/Sidebar";
import { Inspector, InspectorSection } from "@/components/layout/Inspector";

type SettingsSection = "general" | "profiles" | "languages" | "styles" | "cloud";

export default function SettingsWorkspace() {
    const [section, setSection] = useState<SettingsSection>("general");

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
                                <select style={{
                                    padding: '8px 12px',
                                    background: 'rgba(255,255,255,0.05)',
                                    border: '1px solid rgba(255,255,255,0.1)',
                                    borderRadius: 6,
                                    color: '#f5f5f5',
                                    fontSize: 13,
                                }}>
                                    <option value="icelandic">Icelandic</option>
                                    <option value="english">English</option>
                                    <option value="spanish">Spanish</option>
                                </select>
                            }
                            hint="Language used for new jobs by default"
                        />
                        <SettingRow
                            label="Default Subtitle Style"
                            value={
                                <select style={{
                                    padding: '8px 12px',
                                    background: 'rgba(255,255,255,0.05)',
                                    border: '1px solid rgba(255,255,255,0.1)',
                                    borderRadius: 6,
                                    color: '#f5f5f5',
                                    fontSize: 13,
                                }}>
                                    <option value="cinema">Cinema</option>
                                    <option value="minimal">Minimal</option>
                                    <option value="classic">Classic</option>
                                </select>
                            }
                            hint="ASS template for burned subtitles"
                        />
                        <SettingRow
                            label="Auto-Burn on Finalize"
                            value={
                                <div style={{
                                    width: 44,
                                    height: 24,
                                    background: 'rgba(82,139,255,0.5)',
                                    borderRadius: 12,
                                    position: 'relative',
                                    cursor: 'pointer',
                                }}>
                                    <div style={{
                                        width: 20,
                                        height: 20,
                                        background: '#fff',
                                        borderRadius: '50%',
                                        position: 'absolute',
                                        top: 2,
                                        right: 2,
                                        transition: 'all 0.2s',
                                    }} />
                                </div>
                            }
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
                                            <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 2 }}>icelandic • cinema</p>
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
                            {[
                                { code: "is", name: "Icelandic", cps: 14 },
                                { code: "en", name: "English", cps: 17 },
                                { code: "es", name: "Spanish", cps: 17 },
                            ].map((lang) => (
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
                                            <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 2 }}>CPS Target: {lang.cps}</p>
                                        </div>
                                    </div>
                                    <span style={{ fontSize: 12, color: '#6b7280', fontFamily: 'monospace' }}>{lang.code}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                );

            case "styles":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Subtitle Styles</h2>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
                            {[
                                { name: "Cinema", desc: "Box shadow, semi-bold" },
                                { name: "Minimal", desc: "Clean, no background" },
                                { name: "Classic", desc: "Traditional broadcast" },
                            ].map((style) => (
                                <div key={style.name} style={{
                                    padding: 20,
                                    background: 'rgba(255,255,255,0.03)',
                                    border: '1px solid rgba(255,255,255,0.06)',
                                    borderRadius: 12,
                                    textAlign: 'center',
                                }}>
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
                                            textShadow: style.name === "Cinema" ? '0 2px 4px rgba(0,0,0,0.5)' : 'none',
                                        }}>
                                            Sample subtitle
                                        </span>
                                    </div>
                                    <p style={{ fontSize: 14, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>{style.name}</p>
                                    <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 4 }}>{style.desc}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                );

            case "cloud":
                return (
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 600, color: '#f5f5f5', marginBottom: 24 }}>Cloud Pipeline</h2>
                        <SettingRow
                            label="Cloud Translation"
                            value={
                                <div style={{
                                    width: 44,
                                    height: 24,
                                    background: 'rgba(82,139,255,0.5)',
                                    borderRadius: 12,
                                    position: 'relative',
                                    cursor: 'pointer',
                                }}>
                                    <div style={{
                                        width: 20,
                                        height: 20,
                                        background: '#fff',
                                        borderRadius: '50%',
                                        position: 'absolute',
                                        top: 2,
                                        right: 2,
                                    }} />
                                </div>
                            }
                            hint="Use Cloud Run for translation and editing"
                        />
                        <SettingRow
                            label="Region"
                            value={
                                <select style={{
                                    padding: '8px 12px',
                                    background: 'rgba(255,255,255,0.05)',
                                    border: '1px solid rgba(255,255,255,0.1)',
                                    borderRadius: 6,
                                    color: '#f5f5f5',
                                    fontSize: 13,
                                }}>
                                    <option value="us-central1">us-central1</option>
                                    <option value="europe-west1">europe-west1</option>
                                </select>
                            }
                            hint="GCP region for cloud processing"
                        />
                        <SettingRow
                            label="Polish Mode"
                            value={
                                <select style={{
                                    padding: '8px 12px',
                                    background: 'rgba(255,255,255,0.05)',
                                    border: '1px solid rgba(255,255,255,0.1)',
                                    borderRadius: 6,
                                    color: '#f5f5f5',
                                    fontSize: 13,
                                }}>
                                    <option value="review">Review Only</option>
                                    <option value="all">All Jobs</option>
                                </select>
                            }
                            hint="When to apply polish pass"
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
