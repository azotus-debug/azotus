"use client";

import React from "react";
import { useNavigation } from "@/store/navigation";
import type { ViewType } from "@/store/navigation";

const LibraryIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect x="4" y="5" width="4" height="14" rx="1" stroke="currentColor" strokeWidth="1.5" />
    <rect x="10" y="5" width="4" height="14" rx="1" stroke="currentColor" strokeWidth="1.5" />
    <rect x="16" y="5" width="4" height="14" rx="1" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);

const PipelineIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path
      d="M5 6h8a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h10"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const DeliveryIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path
      d="M4 8h10l3 3v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path d="M14 8v3h3" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
  </svg>
);

const GridIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <rect x="4" y="4" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5" />
    <rect x="14" y="4" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5" />
    <rect x="4" y="14" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5" />
    <rect x="14" y="14" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);

const OpsIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path
      d="M12 2L2 7l10 5 10-5-10-5Z"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path
      d="M2 12l10 5 10-5"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path
      d="M2 17l10 5 10-5"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
  </svg>
);

const TABS: Array<{ id: ViewType; label: string; icon: React.ReactNode }> = [
  { id: "library", label: "Library", icon: <LibraryIcon /> },
  { id: "grid", label: "Weekly Grid", icon: <GridIcon /> },
  { id: "ops", label: "Operations", icon: <OpsIcon /> },
  { id: "pipeline", label: "Pipeline", icon: <PipelineIcon /> },
  { id: "delivery", label: "Delivery", icon: <DeliveryIcon /> },
];

export default function NavigationBar() {
  const { activeView, setActiveView } = useNavigation();

  return (
    <nav className="nav-bar" aria-label="Primary">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          className={`nav-tab ${activeView === tab.id ? "active" : ""}`}
          onClick={() => setActiveView(tab.id)}
          type="button"
        >
          <span className="nav-icon">{tab.icon}</span>
          <span className="nav-label">{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}
