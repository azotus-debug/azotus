"use client";

import { useEffect, useState } from "react";
import Button from "@/components/common/Button";
import ImportMediaModal from "@/components/common/ImportMediaModal";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore } from "@/store/programs";
import NavigationBar from "./NavigationBar";
import UnifiedDashboardView from "../views/UnifiedDashboardView";
import UnifiedProgramsView from "../views/UnifiedProgramsView";
import SettingsView from "../views/SettingsView";
import ProgramDetailView from "../views/ProgramDetailView";

export default function AppShell() {
  const { activeView, selectedProgramId, setActiveView } = useNavigation();
  const { fetchPrograms, fetchActiveTracks } = useProgramsStore();
  const [showImportModal, setShowImportModal] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const viewParam = new URLSearchParams(window.location.search).get("view");
    if (!viewParam) return;
    if (viewParam === "dashboard" || viewParam === "programs" || viewParam === "settings") {
      if (activeView !== viewParam) {
        setActiveView(viewParam);
      }
    }
  }, [activeView, setActiveView]);

  const handleImportSuccess = () => {
    // Refresh data after successful import
    fetchPrograms();
    fetchActiveTracks();
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1 className="app-title">Omega Pro</h1>
        <div className="app-header-actions">
          <Button variant="secondary" onClick={() => setShowImportModal(true)}>
            Import Media
          </Button>
          <Button variant="primary">New Program</Button>
        </div>
      </header>

      <NavigationBar />

      <main className="app-content">
        {activeView === "dashboard" && <UnifiedDashboardView />}
        {activeView === "programs" && <UnifiedProgramsView />}
        {activeView === "settings" && <SettingsView />}
      </main>

      {selectedProgramId && <ProgramDetailView programId={selectedProgramId} />}

      <ImportMediaModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        onSuccess={handleImportSuccess}
      />
    </div>
  );
}
