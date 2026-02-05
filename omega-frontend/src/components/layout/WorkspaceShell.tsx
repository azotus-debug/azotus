"use client";

import { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
    Film,
    LayoutGrid,
    Pencil,
    Settings,
    Package,
    Activity,
    Search,
    Bell,
    RefreshCw,
} from "lucide-react";

interface WorkspaceShellProps {
    children: ReactNode;
    sidebar?: ReactNode;
    inspector?: ReactNode;
}

const WORKSPACES = [
    { id: "media", label: "Media", icon: Film, href: "/media" },
    { id: "pipeline", label: "Pipeline", icon: LayoutGrid, href: "/" },
    { id: "edit", label: "Edit", icon: Pencil, href: "/edit" },
    { id: "settings", label: "Settings", icon: Settings, href: "/settings" },
    { id: "deliver", label: "Deliver", icon: Package, href: "/deliver" },
    { id: "monitor", label: "Monitor", icon: Activity, href: "/monitor" },
] as const;

/**
 * WorkspaceShell — Core layout for all Omega Pro workspaces.
 */
export function WorkspaceShell({ children, sidebar, inspector }: WorkspaceShellProps) {
    const pathname = usePathname();

    const getActiveWorkspace = () => {
        if (pathname === "/") return "pipeline";
        if (pathname.startsWith("/media")) return "media";
        if (pathname.startsWith("/edit")) return "edit";
        if (pathname.startsWith("/settings")) return "settings";
        if (pathname.startsWith("/deliver")) return "deliver";
        if (pathname.startsWith("/monitor")) return "monitor";
        return "pipeline";
    };

    const activeWorkspace = getActiveWorkspace();

    return (
        <div className="flex h-screen flex-col bg-[#0f0f12] text-[#f5f5f5] overflow-hidden">
            {/* Top Bar — taller with more padding */}
            <header className="h-14 flex items-center justify-between px-5 border-b border-[rgba(255,255,255,0.06)] bg-[#18181b] shrink-0">
                {/* Left: Logo with more spacing */}
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-[#528BFF] flex items-center justify-center">
                            <Film className="w-4 h-4 text-white" />
                        </div>
                        <span className="text-[15px] font-semibold tracking-tight">Omega Pro</span>
                    </div>
                </div>

                {/* Center: Search — wider */}
                <div className="flex-1 max-w-lg mx-10">
                    <div className="relative">
                        <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#6b7280]" />
                        <input
                            type="text"
                            placeholder="Search jobs..."
                            className="w-full bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.08)] rounded-lg pl-10 pr-4 py-2 text-[13px] text-[#f5f5f5] placeholder:text-[#6b7280] focus:border-[rgba(82,139,255,0.5)] focus:outline-none transition"
                        />
                        <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[11px] text-[#6b7280] font-medium">⌘K</span>
                    </div>
                </div>

                {/* Right: Actions with better spacing */}
                <div className="flex items-center gap-2">
                    <button className="p-2.5 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[#9ca3af] hover:text-[#f5f5f5] transition">
                        <RefreshCw className="w-4 h-4" />
                    </button>
                    <button className="p-2.5 rounded-lg hover:bg-[rgba(255,255,255,0.06)] text-[#9ca3af] hover:text-[#f5f5f5] transition relative">
                        <Bell className="w-4 h-4" />
                        <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-[#f59e0b]"></span>
                    </button>
                </div>
            </header>

            {/* Main Content Area */}
            <div className="flex-1 flex min-h-0" style={{ paddingBottom: 56 }}>
                {/* Sidebar */}
                {sidebar && (
                    <aside className="w-56 border-r border-[rgba(255,255,255,0.06)] bg-[#18181b] shrink-0 overflow-y-auto">
                        {sidebar}
                    </aside>
                )}

                {/* Main Content */}
                <main className="flex-1 overflow-y-auto bg-[#0f0f12]">
                    {children}
                </main>

                {/* Inspector Panel */}
                {inspector && (
                    <aside className="w-80 border-l border-[rgba(255,255,255,0.06)] bg-[#18181b] shrink-0 overflow-y-auto">
                        {inspector}
                    </aside>
                )}
            </div>

            {/* Workspace Tabs — generous spacing */}
            <nav
                className="flex items-center justify-center border-t border-[rgba(255,255,255,0.06)] bg-[#0a0a0c]"
                style={{ height: 56, gap: 40, position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 100 }}
            >
                {WORKSPACES.map((ws) => {
                    const Icon = ws.icon;
                    const isActive = activeWorkspace === ws.id;
                    return (
                        <Link
                            key={ws.id}
                            href={ws.href}
                            className={`flex items-center rounded-lg text-[13px] font-medium transition ${isActive
                                ? "bg-[rgba(82,139,255,0.12)] text-[#528BFF]"
                                : "text-[#6b7280] hover:text-[#f5f5f5] hover:bg-[rgba(255,255,255,0.05)]"
                                }`}
                            style={{ gap: 10, padding: '10px 16px' }}
                        >
                            <Icon style={{ width: 18, height: 18 }} />
                            <span>{ws.label}</span>
                        </Link>
                    );
                })}
            </nav>
        </div>
    );
}
