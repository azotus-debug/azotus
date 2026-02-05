"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import {
    Pencil,
    FileText,
    AlertCircle,
    ExternalLink
} from "lucide-react";
import { WorkspaceShell } from "@/components/layout/WorkspaceShell";
import { Sidebar, SidebarSection, SidebarItem } from "@/components/layout/Sidebar";
import { Inspector, InspectorSection, InspectorRow } from "@/components/layout/Inspector";

interface Job {
    file_stem: string;
    stage: string;
    status: string;
    updated_at: string;
    meta?: Record<string, unknown>;
}

export default function EditWorkspace() {
    const router = useRouter();
    const [jobs, setJobs] = useState<Job[]>([]);
    const [filter, setFilter] = useState<"editable" | "review" | "all">("editable");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);

    const fetchJobs = async () => {
        try {
            const res = await fetch("/api/jobs");
            const data = await res.json();
            setJobs(data);
        } catch (err) {
            console.error("Failed to fetch jobs", err);
        }
    };

    useEffect(() => {
        fetchJobs();
        const interval = setInterval(fetchJobs, 10000);
        return () => clearInterval(interval);
    }, []);

    // Filter jobs that are in editable stages
    const editableJobs = useMemo(() => {
        return jobs.filter((job) =>
            ["TRANSLATE", "REVIEW", "FINALIZE", "DONE"].includes(job.stage) ||
            job.stage.includes("TRANSLAT") ||
            job.stage.includes("EDIT")
        );
    }, [jobs]);

    const reviewJobs = useMemo(() => {
        return jobs.filter((job) => job.status === "needs_review" || job.stage === "REVIEW");
    }, [jobs]);

    const displayedJobs = useMemo(() => {
        if (filter === "editable") return editableJobs;
        if (filter === "review") return reviewJobs;
        return jobs;
    }, [filter, jobs, editableJobs, reviewJobs]);

    const handleOpenEditor = (fileStem: string) => {
        router.push(`/editor/${encodeURIComponent(fileStem)}`);
    };

    // Sidebar
    const sidebarContent = (
        <Sidebar>
            <SidebarSection title="Edit Queue">
                <SidebarItem
                    label="Editable"
                    count={editableJobs.length}
                    isActive={filter === "editable"}
                    onClick={() => setFilter("editable")}
                    icon={<Pencil style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Needs Review"
                    count={reviewJobs.length}
                    isActive={filter === "review"}
                    onClick={() => setFilter("review")}
                    icon={<AlertCircle style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="All Jobs"
                    count={jobs.length}
                    isActive={filter === "all"}
                    onClick={() => setFilter("all")}
                    icon={<FileText style={{ width: 14, height: 14 }} />}
                />
            </SidebarSection>
        </Sidebar>
    );

    // Inspector
    const inspectorContent = selectedJob ? (
        <Inspector title="Edit Details">
            <InspectorSection title="Job Info">
                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                <InspectorRow label="Updated">
                    {new Date(selectedJob.updated_at).toLocaleString()}
                </InspectorRow>
            </InspectorSection>
            <InspectorSection title="Actions">
                <div style={{ padding: '8px 0' }}>
                    <button
                        onClick={() => handleOpenEditor(selectedJob.file_stem)}
                        style={{
                            width: '100%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 8,
                            padding: '12px 16px',
                            background: '#528BFF',
                            color: '#fff',
                            border: 'none',
                            borderRadius: 8,
                            fontSize: 13,
                            fontWeight: 500,
                            cursor: 'pointer',
                        }}
                    >
                        <ExternalLink style={{ width: 16, height: 16 }} />
                        Open Editor
                    </button>
                </div>
            </InspectorSection>
        </Inspector>
    ) : (
        <Inspector title="Edit Details">
            <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: '#6b7280' }}>
                Select a job to edit
            </div>
        </Inspector>
    );

    return (
        <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
            <div style={{ padding: 24 }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, color: '#9ca3af', marginBottom: 16, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {filter === "editable" ? "Editable Jobs" : filter === "review" ? "Needs Review" : "All Jobs"}
                </h2>

                {displayedJobs.length === 0 ? (
                    <div style={{
                        padding: 48,
                        textAlign: 'center',
                        color: '#6b7280',
                        fontSize: 14,
                        background: 'rgba(255,255,255,0.02)',
                        borderRadius: 12,
                        border: '1px solid rgba(255,255,255,0.06)'
                    }}>
                        No jobs available for editing
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {displayedJobs.map((job) => (
                            <div
                                key={job.file_stem}
                                onClick={() => setSelectedJob(job)}
                                onDoubleClick={() => handleOpenEditor(job.file_stem)}
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 16,
                                    padding: '16px 20px',
                                    borderRadius: 10,
                                    background: selectedJob?.file_stem === job.file_stem
                                        ? 'rgba(82,139,255,0.12)'
                                        : 'rgba(255,255,255,0.03)',
                                    border: selectedJob?.file_stem === job.file_stem
                                        ? '1px solid rgba(82,139,255,0.3)'
                                        : '1px solid rgba(255,255,255,0.06)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s ease',
                                }}
                            >
                                <div style={{
                                    width: 44,
                                    height: 44,
                                    borderRadius: 8,
                                    background: job.status === "needs_review" ? 'rgba(245,158,11,0.15)' : 'rgba(82,139,255,0.15)',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    flexShrink: 0
                                }}>
                                    {job.status === "needs_review" ? (
                                        <AlertCircle style={{ width: 22, height: 22, color: '#f59e0b' }} />
                                    ) : (
                                        <FileText style={{ width: 22, height: 22, color: '#528BFF' }} />
                                    )}
                                </div>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <h3 style={{
                                        fontSize: 14,
                                        fontWeight: 500,
                                        color: '#f5f5f5',
                                        margin: 0,
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap'
                                    }}>
                                        {job.file_stem}
                                    </h3>
                                    <p style={{ fontSize: 12, color: '#6b7280', margin: 0, marginTop: 4 }}>
                                        {job.stage} • {job.status}
                                    </p>
                                </div>
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        handleOpenEditor(job.file_stem);
                                    }}
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 6,
                                        padding: '8px 14px',
                                        background: 'rgba(82,139,255,0.15)',
                                        color: '#528BFF',
                                        border: 'none',
                                        borderRadius: 6,
                                        fontSize: 12,
                                        fontWeight: 500,
                                        cursor: 'pointer',
                                    }}
                                >
                                    <Pencil style={{ width: 14, height: 14 }} />
                                    Edit
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                <p style={{ fontSize: 11, color: '#6b7280', marginTop: 16 }}>
                    Double-click a job to open the editor, or select and click &quot;Open Editor&quot;
                </p>
            </div>
        </WorkspaceShell>
    );
}
