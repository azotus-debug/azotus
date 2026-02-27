"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle } from "lucide-react";

export default function EditorError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    const params = useParams();
    const jobId = params?.jobId as string;

    useEffect(() => {
        console.error(`[Omega Pro] Editor error (job: ${jobId}):`, error);
    }, [error, jobId]);

    const displayMessage =
        error.message && error.message.length > 200
            ? error.message.slice(0, 200) + "..."
            : error.message || "An unexpected error occurred in the editor.";

    return (
        <div className="min-h-screen bg-[rgb(10,10,12)] flex items-center justify-center px-6">
            <div className="max-w-md w-full text-center space-y-6">
                <div className="flex justify-center">
                    <div className="rounded-full bg-red-500/10 p-4">
                        <AlertTriangle className="h-10 w-10 text-red-400" />
                    </div>
                </div>

                <div className="space-y-2">
                    <h1 className="text-2xl font-semibold text-white tracking-tight">
                        Workstation Error
                    </h1>
                    <p className="text-sm text-zinc-400 leading-relaxed">
                        {displayMessage}
                    </p>
                    {jobId && (
                        <p className="text-xs text-zinc-500 font-mono mt-1">
                            Job: {jobId}
                        </p>
                    )}
                    {error.digest && (
                        <p className="text-xs text-zinc-600 font-mono mt-1">
                            Digest: {error.digest}
                        </p>
                    )}
                </div>

                <div className="flex items-center justify-center gap-3 pt-2">
                    <button
                        onClick={reset}
                        className="px-5 py-2.5 text-sm font-medium rounded-lg bg-white text-black hover:bg-zinc-200 transition-colors"
                    >
                        Try Again
                    </button>
                    <Link
                        href="/"
                        className="px-5 py-2.5 text-sm font-medium rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors"
                    >
                        Back to Library
                    </Link>
                </div>
            </div>
        </div>
    );
}
