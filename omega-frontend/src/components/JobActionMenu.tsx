"use client";

import { useState } from "react";
import { MoreVertical, Trash2, RefreshCw, RotateCcw, Loader2 } from "lucide-react";

interface JobActionMenuProps {
    fileStem: string;
    onActionComplete: () => void;
}

export function JobActionMenu({ fileStem, onActionComplete }: JobActionMenuProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState(false);

    const handleAction = async (action: string) => {
        if (action === "delete_job" && !confirm(`PERMANENTLY DELETE PROJECT ${fileStem}?`)) return;
        setLoading(true);
        try {
            await fetch("/api/action", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action, file_stem: fileStem })
            });
            setIsOpen(false);
            onActionComplete();
        } catch { alert("Action Failed"); }
        finally { setLoading(false); }
    };

    return (
        <div className="relative">
            <button
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); setIsOpen(!isOpen); }}
                className="p-2 rounded-full hover:bg-omega-surface text-omega-text-secondary hover:text-white transition-colors"
            >
                {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MoreVertical className="w-3.5 h-3.5" />}
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" onClick={(e) => { e.preventDefault(); e.stopPropagation(); setIsOpen(false); }} />
                    <div className="absolute right-0 top-8 w-44 bg-omega-panel/95 border border-omega-border/70 rounded-xl shadow-2xl z-50 overflow-hidden py-1">
                        <button
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleAction("retry_translate"); }}
                            className="w-full text-left px-3 py-2 text-xs text-omega-text-primary hover:bg-omega-surface/70 flex items-center gap-2"
                        >
                            <RefreshCw className="w-3 h-3" /> Retry Task
                        </button>
                        <button
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleAction("reset_review"); }}
                            className="w-full text-left px-3 py-2 text-xs text-omega-text-primary hover:bg-omega-surface/70 flex items-center gap-2"
                        >
                            <RotateCcw className="w-3 h-3" /> Reset Review
                        </button>
                        <div className="h-px bg-omega-border/70 my-1" />
                        <button
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleAction("delete_job"); }}
                            className="w-full text-left px-3 py-2 text-xs text-omega-alert hover:bg-omega-alert/10 flex items-center gap-2"
                        >
                            <Trash2 className="w-3 h-3" /> Delete Project
                        </button>
                    </div>
                </>
            )}
        </div>
    );
}
