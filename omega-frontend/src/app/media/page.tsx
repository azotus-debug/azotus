"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import {
    UploadCloud,
    Loader2,
    Film,
    FileVideo,
    Clock,
    CheckCircle2,
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

export default function MediaWorkspace() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [uploading, setUploading] = useState(false);
    const [dragActive, setDragActive] = useState(false);
    const [filter, setFilter] = useState<"all" | "recent" | "processing">("all");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Fetch jobs
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

    // Filter for recent imports (jobs in early stages)
    const recentImports = useMemo(() => {
        const now = Date.now();
        const oneDayAgo = now - 24 * 60 * 60 * 1000;
        return jobs.filter((job) => {
            const updated = new Date(job.updated_at).getTime();
            return updated > oneDayAgo;
        }).sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }, [jobs]);

    const processingJobs = useMemo(() => {
        return jobs.filter((job) =>
            job.stage !== "DONE" && job.stage !== "DELIVER"
        );
    }, [jobs]);

    const displayedJobs = useMemo(() => {
        if (filter === "recent") return recentImports.slice(0, 10);
        if (filter === "processing") return processingJobs;
        return jobs;
    }, [filter, jobs, recentImports, processingJobs]);

    // Upload handlers
    const handleDrag = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "dragenter" || e.type === "dragover") {
            setDragActive(true);
        } else if (e.type === "dragleave") {
            setDragActive(false);
        }
    };

    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);

        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            await uploadFiles(e.dataTransfer.files);
        }
    };

    const handleSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            await uploadFiles(e.target.files);
        }
    };

    const uploadFiles = async (files: FileList) => {
        setUploading(true);
        try {
            for (let i = 0; i < files.length; i++) {
                const formData = new FormData();
                formData.append("file", files[i]);
                const res = await fetch("/api/upload", { method: "POST", body: formData });
                if (!res.ok) throw new Error("Upload Failed");
            }
            fetchJobs();
        } catch (err) {
            console.error(err);
            alert("Import Failed");
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const getStageIcon = (stage: string) => {
        if (stage === "DONE") return <CheckCircle2 style={{ width: 14, height: 14, color: '#22c55e' }} />;
        if (stage === "INGEST") return <Clock style={{ width: 14, height: 14, color: '#f59e0b' }} />;
        return <Loader2 style={{ width: 14, height: 14, color: '#528BFF' }} />;
    };

    // Sidebar
    const sidebarContent = (
        <Sidebar>
            <SidebarSection title="Library">
                <SidebarItem
                    label="All Media"
                    count={jobs.length}
                    isActive={filter === "all"}
                    onClick={() => setFilter("all")}
                    icon={<Film style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Recent Imports"
                    count={recentImports.length}
                    isActive={filter === "recent"}
                    onClick={() => setFilter("recent")}
                    icon={<Clock style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Processing"
                    count={processingJobs.length}
                    isActive={filter === "processing"}
                    onClick={() => setFilter("processing")}
                    icon={<Loader2 style={{ width: 14, height: 14 }} />}
                />
            </SidebarSection>
        </Sidebar>
    );

    // Inspector
    const inspectorContent = selectedJob ? (
        <Inspector title="Media Info">
            <InspectorSection title="Details">
                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                <InspectorRow label="Updated">
                    {new Date(selectedJob.updated_at).toLocaleString()}
                </InspectorRow>
            </InspectorSection>
        </Inspector>
    ) : (
        <Inspector title="Media Info">
            <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: '#6b7280' }}>
                Select a file to view details
            </div>
        </Inspector>
    );

    return (
        <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
            <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24 }}>
                {/* Upload Zone */}
                <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleSelect}
                    style={{ display: 'none' }}
                    multiple
                    accept="video/*,audio/*,.mp4,.mov,.mp3,.wav,.srt"
                />
                <div
                    onDragEnter={handleDrag}
                    onDragLeave={handleDrag}
                    onDragOver={handleDrag}
                    onDrop={handleDrop}
                    onClick={() => fileInputRef.current?.click()}
                    style={{
                        border: `2px dashed ${dragActive ? '#528BFF' : 'rgba(255,255,255,0.1)'}`,
                        borderRadius: 12,
                        padding: 48,
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: 16,
                        cursor: 'pointer',
                        background: dragActive ? 'rgba(82,139,255,0.08)' : 'rgba(255,255,255,0.02)',
                        transition: 'all 0.2s ease',
                    }}
                >
                    {uploading ? (
                        <Loader2 style={{ width: 48, height: 48, color: '#528BFF', animation: 'spin 1s linear infinite' }} />
                    ) : (
                        <UploadCloud style={{ width: 48, height: 48, color: dragActive ? '#528BFF' : '#6b7280' }} />
                    )}
                    <div style={{ textAlign: 'center' }}>
                        <p style={{ fontSize: 16, fontWeight: 500, color: '#f5f5f5', margin: 0 }}>
                            {uploading ? "Uploading..." : "Drop files here or click to import"}
                        </p>
                        <p style={{ fontSize: 13, color: '#6b7280', marginTop: 8 }}>
                            Video, Audio, or SRT files
                        </p>
                    </div>
                </div>

                {/* Media List */}
                <div>
                    <h2 style={{ fontSize: 14, fontWeight: 600, color: '#9ca3af', marginBottom: 16, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        {filter === "recent" ? "Recent Imports" : filter === "processing" ? "Processing" : "All Media"}
                    </h2>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {displayedJobs.length === 0 ? (
                            <div style={{ padding: 32, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
                                No media files found
                            </div>
                        ) : (
                            displayedJobs.map((job) => (
                                <div
                                    key={job.file_stem}
                                    onClick={() => setSelectedJob(job)}
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 12,
                                        padding: '12px 16px',
                                        borderRadius: 8,
                                        background: selectedJob?.file_stem === job.file_stem
                                            ? 'rgba(82,139,255,0.12)'
                                            : 'rgba(255,255,255,0.03)',
                                        border: selectedJob?.file_stem === job.file_stem
                                            ? '1px solid rgba(82,139,255,0.3)'
                                            : '1px solid transparent',
                                        cursor: 'pointer',
                                        transition: 'all 0.15s ease',
                                    }}
                                >
                                    <FileVideo style={{ width: 20, height: 20, color: '#528BFF', flexShrink: 0 }} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <p style={{ fontSize: 13, fontWeight: 500, color: '#f5f5f5', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {job.file_stem}
                                        </p>
                                        <p style={{ fontSize: 11, color: '#6b7280', margin: 0, marginTop: 2 }}>
                                            {job.stage} • {job.status}
                                        </p>
                                    </div>
                                    {getStageIcon(job.stage)}
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>
        </WorkspaceShell>
    );
}
