"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";

export default function GlobalError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        console.error("[Omega Pro] Unhandled error:", error);
    }, [error]);

    const displayMessage =
        error.message && error.message.length > 200
            ? error.message.slice(0, 200) + "..."
            : error.message || "An unexpected error occurred.";

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
                        Something went wrong
                    </h1>
                    <p className="text-sm text-zinc-400 leading-relaxed">
                        {displayMessage}
                    </p>
                    {error.digest && (
                        <p className="text-xs text-zinc-600 font-mono mt-2">
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
                        Back to Home
                    </Link>
                </div>
            </div>
        </div>
    );
}
