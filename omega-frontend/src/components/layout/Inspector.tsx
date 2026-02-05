"use client";

import { ReactNode, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface InspectorSectionProps {
    title: string;
    children: ReactNode;
    defaultOpen?: boolean;
}

/**
 * InspectorSection — Collapsible section within the Inspector panel.
 * Follows DaVinci Resolve's render settings pattern.
 */
export function InspectorSection({ title, children, defaultOpen = true }: InspectorSectionProps) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    return (
        <div className="border-b border-[rgba(255,255,255,0.06)]">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="w-full flex items-center gap-2 px-3 py-2 text-[11px] font-medium text-[#9ca3af] hover:text-[#f5f5f5] hover:bg-[rgba(255,255,255,0.03)] transition"
            >
                {isOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                {title}
            </button>
            {isOpen && <div className="px-3 pb-3">{children}</div>}
        </div>
    );
}

interface InspectorRowProps {
    label: string;
    children: ReactNode;
}

/**
 * InspectorRow — Label + value row for property display.
 */
export function InspectorRow({ label, children }: InspectorRowProps) {
    return (
        <div className="flex items-center justify-between py-1.5 text-[11px]">
            <span className="text-[#6b7280]">{label}</span>
            <span className="text-[#f5f5f5] text-right max-w-[60%] truncate">{children}</span>
        </div>
    );
}

interface InspectorProps {
    children: ReactNode;
    title?: string;
}

/**
 * Inspector — Right panel container for showing details of selected items.
 * 
 * Data Flow:
 * - Receives selected item data from parent workspace
 * - Displays properties in collapsible sections
 * - Actions trigger API calls back to dashboard.py
 */
export function Inspector({ children, title = "Inspector" }: InspectorProps) {
    return (
        <div className="h-full flex flex-col">
            {/* Header */}
            <div className="h-9 flex items-center px-3 border-b border-[rgba(255,255,255,0.06)] bg-[rgba(255,255,255,0.02)]">
                <span className="text-[11px] font-medium text-[#9ca3af] uppercase tracking-wider">{title}</span>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto">
                {children}
            </div>
        </div>
    );
}
