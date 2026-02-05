"use client";

import { useState, useEffect, useMemo } from "react";
import {
    Package,
    CheckCircle2,
    FolderOpen,
    FileText,
    Film,
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

export default function DeliverWorkspace() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [filter, setFilter] = useState<"ready" | "delivered" | "all">("ready");
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
    const [marking, setMarking] = useState(false);

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

    // Filter jobs by delivery status
    const readyJobs = useMemo(() => {
        return jobs.filter((job) => job.stage === "DONE" && job.status !== "DELIVERED");
    }, [jobs]);

    const deliveredJobs = useMemo(() => {
        return jobs.filter((job) => job.status === "DELIVERED");
    }, [jobs]);

    const displayedJobs = useMemo(() => {
        if (filter === "ready") return readyJobs;
        if (filter === "delivered") return deliveredJobs;
        return jobs.filter((job) => job.stage === "DONE" || job.status === "DELIVERED");
    }, [filter, jobs, readyJobs, deliveredJobs]);

    // Mark as delivered
    const handleMarkDelivered = async (fileStem: string) => {
        setMarking(true);
        try {
            const res = await fetch("/api/mark_delivered", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ file_stem: fileStem }),
            });
            if (res.ok) {
                fetchJobs();
                setSelectedJob(null);
            } else {
                alert("Failed to mark as delivered");
            }
        } catch (err) {
            console.error(err);
            alert("Failed to mark as delivered");
        } finally {
            setMarking(false);
        }
    };

    // Sidebar
    const sidebarContent = (
        <Sidebar>
            <SidebarSection title="Deliverables">
                <SidebarItem
                    label="Ready"
                    count={readyJobs.length}
                    isActive={filter === "ready"}
                    onClick={() => setFilter("ready")}
                    icon={<Package style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="Delivered"
                    count={deliveredJobs.length}
                    isActive={filter === "delivered"}
                    onClick={() => setFilter("delivered")}
                    icon={<CheckCircle2 style={{ width: 14, height: 14 }} />}
                />
                <SidebarItem
                    label="All"
                    count={readyJobs.length + deliveredJobs.length}
                    isActive={filter === "all"}
                    onClick={() => setFilter("all")}
                    icon={<FolderOpen style={{ width: 14, height: 14 }} />}
                />
            </SidebarSection>
        </Sidebar>
    );

    // Inspector
    const inspectorContent = selectedJob ? (
        <Inspector title="Delivery Details">
            <InspectorSection title="Output">
                <InspectorRow label="Name">{selectedJob.file_stem}</InspectorRow>
                <InspectorRow label="Stage">{selectedJob.stage}</InspectorRow>
                <InspectorRow label="Status">{selectedJob.status}</InspectorRow>
                <InspectorRow label="Updated">
                    {new Date(selectedJob.updated_at).toLocaleString()}
                </InspectorRow>
            </InspectorSection>
            <InspectorSection title="Actions">
                <div style={{ padding: '8px 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {selectedJob.status !== "DELIVERED" && (
                        <button
                            onClick={() => handleMarkDelivered(selectedJob.file_stem)}
                            disabled={marking}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                gap: 8,
                                padding: '10px 16px',
                                background: '#22c55e',
                                color: '#fff',
                                border: 'none',
                                borderRadius: 6,
                                fontSize: 13,
                                fontWeight: 500,
                                cursor: marking ? 'not-allowed' : 'pointer',
                                opacity: marking ? 0.6 : 1,
                            }}
                        >
                            <CheckCircle2 style={{ width: 16, height: 16 }} />
                            {marking ? "Marking..." : "Mark Delivered"}
                        </button>
                    )}
                </div>
            </InspectorSection>
        </Inspector>
    ) : (
        <Inspector title="Delivery Details">
            <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: '#6b7280' }}>
                Select a job to view details
            </div>
        </Inspector>
    );

    return (
        <WorkspaceShell sidebar={sidebarContent} inspector={inspectorContent}>
            <div style={{ padding: 24 }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, color: '#9ca3af', marginBottom: 16, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {filter === "ready" ? "Ready for Delivery" : filter === "delivered" ? "Delivered" : "All Deliverables"}
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
                        {filter === "ready" ? "No jobs ready for delivery" : "No delivered jobs"}
                    </div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
                        {displayedJobs.map((job) => (
                            <div
                                key={job.file_stem}
                                onClick={() => setSelectedJob(job)}
                                style={{
                                    padding: 20,
                                    borderRadius: 12,
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
                                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                                    <div style={{
                                        width: 44,
                                        height: 44,
                                        borderRadius: 8,
                                        background: job.status === "DELIVERED" ? 'rgba(34,197,94,0.15)' : 'rgba(82,139,255,0.15)',
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        flexShrink: 0
                                    }}>
                                        {job.status === "DELIVERED" ? (
                                            <CheckCircle2 style={{ width: 22, height: 22, color: '#22c55e' }} />
                                        ) : (
                                            <Package style={{ width: 22, height: 22, color: '#528BFF' }} />
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
                                            {job.status === "DELIVERED" ? "Delivered" : "Ready"}
                                        </p>
                                        <p style={{ fontSize: 11, color: '#6b7280', margin: 0, marginTop: 4 }}>
                                            {new Date(job.updated_at).toLocaleString()}
                                        </p>
                                    </div>
                                </div>

                                {/* Output files indicator */}
                                <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                                    <div style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 4,
                                        padding: '4px 8px',
                                        background: 'rgba(255,255,255,0.05)',
                                        borderRadius: 4,
                                        fontSize: 11,
                                        color: '#9ca3af'
                                    }}>
                                        <Film style={{ width: 12, height: 12 }} /> MP4
                                    </div>
                                    <div style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 4,
                                        padding: '4px 8px',
                                        background: 'rgba(255,255,255,0.05)',
                                        borderRadius: 4,
                                        fontSize: 11,
                                        color: '#9ca3af'
                                    }}>
                                        <FileText style={{ width: 12, height: 12 }} /> SRT
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </WorkspaceShell>
    );
}
