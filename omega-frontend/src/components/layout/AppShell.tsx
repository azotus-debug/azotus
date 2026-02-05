"use client";

import { useState } from "react";
import Button from "@/components/common/Button";
import ImportMediaModal from "@/components/common/ImportMediaModal";
import { useNavigation } from "@/store/navigation";
import { useProgramsStore } from "@/store/programs";
import NavigationBar from "./NavigationBar";
import LibraryView from "../views/LibraryView";
import PipelineView from "../views/PipelineView";
import DeliveryView from "../views/DeliveryView";
import WeeklyGridView from "../views/WeeklyGridView";
import OperationsView from "../views/OperationsView";
import ProgramDetailView from "../views/ProgramDetailView";

export default function AppShell() {
  const { activeView, selectedProgramId } = useNavigation();
  const { fetchPrograms, fetchActiveTracks } = useProgramsStore();
  const [showImportModal, setShowImportModal] = useState(false);

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
          <Button variant="ghost" onClick={() => setShowImportModal(true)}>
            Import Media
          </Button>
          <Button>New Program</Button>
        </div>
      </header>

      <NavigationBar />

      <main className="app-content">
        {activeView === "library" && <LibraryView />}
        {activeView === "grid" && <WeeklyGridView />}
        {activeView === "ops" && <OperationsView />}
        {activeView === "pipeline" && <PipelineView />}
        {activeView === "delivery" && <DeliveryView />}
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
