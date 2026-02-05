"use client";

import { ReactNode } from "react";

interface SidebarSectionProps {
    title: string;
    children: ReactNode;
}

/**
 * SidebarSection — Grouped section within the context sidebar.
 */
export function SidebarSection({ title, children }: SidebarSectionProps) {
    return (
        <div style={{ padding: '16px 0' }}>
            <div
                style={{
                    padding: '0 16px 12px',
                    fontSize: 11,
                    fontWeight: 600,
                    color: '#6b7280',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em'
                }}
            >
                {title}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {children}
            </div>
        </div>
    );
}

interface SidebarItemProps {
    label: string;
    count?: number;
    isActive?: boolean;
    onClick?: () => void;
    icon?: ReactNode;
}

/**
 * SidebarItem — Clickable filter/navigation item.
 */
export function SidebarItem({ label, count, isActive, onClick, icon }: SidebarItemProps) {
    return (
        <button
            onClick={onClick}
            style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 16px',
                fontSize: 13,
                fontWeight: 500,
                borderRadius: 6,
                border: 'none',
                cursor: 'pointer',
                transition: 'background 0.15s, color 0.15s',
                background: isActive ? 'rgba(82, 139, 255, 0.12)' : 'transparent',
                color: isActive ? '#528BFF' : '#9ca3af',
            }}
            onMouseEnter={(e) => {
                if (!isActive) {
                    e.currentTarget.style.background = 'rgba(255, 255, 255, 0.04)';
                    e.currentTarget.style.color = '#f5f5f5';
                }
            }}
            onMouseLeave={(e) => {
                if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                    e.currentTarget.style.color = '#9ca3af';
                }
            }}
        >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {icon}
                {label}
            </span>
            {count !== undefined && count > 0 && (
                <span style={{
                    fontSize: 11,
                    fontWeight: 500,
                    fontVariantNumeric: 'tabular-nums',
                    color: isActive ? '#528BFF' : '#6b7280'
                }}>
                    {count}
                </span>
            )}
        </button>
    );
}

interface SidebarProps {
    children: ReactNode;
}

/**
 * Sidebar — Left panel container for context-specific navigation.
 */
export function Sidebar({ children }: SidebarProps) {
    return (
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column', padding: '8px 0' }}>
            {children}
        </div>
    );
}
