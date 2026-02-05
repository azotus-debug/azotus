"use client";

import { useState, useEffect, useRef } from "react";
import { Terminal, X, Filter } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export function LogsViewer() {
    const [isOpen, setIsOpen] = useState(false);
    const [logs, setLogs] = useState("");
    const [loading, setLoading] = useState(false);
    const [filter, setFilter] = useState("");
    const logEndRef = useRef<HTMLDivElement>(null);

    const fetchLogs = async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/logs?lines=200");
            const text = await res.text();
            setLogs(text);
        } catch {
            setLogs("Connection Error.");
        } finally {
            setLoading(false);
        }
    };

    const handleRestart = async () => {
        if (!confirm("Restart Manager? Jobs will pause briefly.")) return;
        try {
            await fetch("/api/action", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "restart_manager" }),
            });
        } catch {
            alert("Failed.");
        }
    };

    useEffect(() => {
        if (isOpen) {
            fetchLogs();
            const interval = setInterval(fetchLogs, 3000);
            return () => clearInterval(interval);
        }
    }, [isOpen]);

    useEffect(() => {
        if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }, [logs, isOpen]);

    const filteredLogs = filter
        ? logs.split("\n").filter(line => line.toLowerCase().includes(filter.toLowerCase())).join("\n")
        : logs;

    return (
        <>
            <button
                onClick={() => setIsOpen(true)}
                className="btn btn-ghost p-2"
                title="System Console"
            >
                <Terminal className="w-4 h-4" />
            </button>

            <AnimatePresence>
                {isOpen && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-8"
                        onClick={(e) => e.target === e.currentTarget && setIsOpen(false)}
                    >
                        <motion.div
                            initial={{ scale: 0.96, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.96, opacity: 0 }}
                            transition={{ duration: 0.15 }}
                            className="surface-1 w-full max-w-4xl h-[80vh] rounded-xl border border-subtle flex flex-col overflow-hidden shadow-2xl"
                        >
                            {/* Header */}
                            <div className="flex items-center justify-between px-4 py-3 border-b border-subtle surface-2">
                                <div className="flex items-center gap-3">
                                    <Terminal className="w-4 h-4 text-cyan" />
                                    <span className="text-sm font-medium text-primary">System Console</span>
                                    <span className={`pill text-[10px] ${loading ? "bg-[rgba(251,191,36,0.12)] text-amber" : "bg-[rgba(52,211,153,0.12)] text-emerald"}`}>
                                        {loading ? "Syncing" : "Live"}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={handleRestart}
                                        className="btn text-xs py-1 px-3 bg-[rgba(251,113,133,0.12)] text-rose border-[rgba(251,113,133,0.2)] hover:bg-[rgba(251,113,133,0.2)]"
                                    >
                                        Force Restart
                                    </button>
                                    <button onClick={() => setIsOpen(false)} className="btn btn-ghost p-1.5">
                                        <X className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            {/* Filter */}
                            <div className="px-4 py-2 border-b border-subtle">
                                <div className="relative">
                                    <Filter className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                                    <input
                                        type="text"
                                        placeholder="Filter logs..."
                                        className="input pl-9 py-2 w-64 text-xs"
                                        value={filter}
                                        onChange={(e) => setFilter(e.target.value)}
                                    />
                                </div>
                            </div>

                            {/* Logs */}
                            <div className="flex-1 overflow-auto p-4 bg-[rgb(10,10,12)] font-mono text-xs leading-relaxed text-secondary">
                                <pre className="whitespace-pre-wrap">
                                    {filteredLogs || "Initializing..."}
                                </pre>
                                <div ref={logEndRef} />
                            </div>

                            {/* Footer */}
                            <div className="px-4 py-2 border-t border-subtle text-[10px] text-muted flex justify-between">
                                <span>Port 8080</span>
                                <span>{logs.split("\n").length} lines</span>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
}
