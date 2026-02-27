"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { useNavigation } from "@/store/navigation";
import type { ViewType } from "@/store/navigation";
import { LayoutDashboard, Library, Settings } from "lucide-react";

const TABS: Array<{ id: ViewType; label: string; icon: React.ReactNode }> = [
  { id: "dashboard", label: "Dashboard", icon: <LayoutDashboard size={18} /> },
  { id: "programs", label: "Programs", icon: <Library size={18} /> },
  { id: "settings", label: "Settings", icon: <Settings size={18} /> },
];

export default function NavigationBar() {
  const { activeView, setActiveView } = useNavigation();
  const router = useRouter();

  const handleSelect = (view: ViewType) => {
    setActiveView(view);
    const params = new URLSearchParams(typeof window !== "undefined" ? window.location.search : "");
    params.set("view", view);
    router.replace(`/?${params.toString()}`, { scroll: false });
  };

  return (
    <nav className="nav-bar" aria-label="Primary">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          className={`nav-tab ${activeView === tab.id ? "active" : ""}`}
          onClick={() => handleSelect(tab.id)}
          type="button"
        >
          <span className="nav-icon">{tab.icon}</span>
          <span className="nav-label">{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}
