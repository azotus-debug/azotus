import { create } from "zustand";

type ViewType = "library" | "pipeline" | "delivery" | "grid" | "ops";

interface NavigationStore {
  activeView: ViewType;
  setActiveView: (view: ViewType) => void;
  selectedProgramId: string | null;
  selectProgram: (id: string) => void;
  clearSelection: () => void;
}

export const useNavigation = create<NavigationStore>((set) => ({
  activeView: "library",
  setActiveView: (view) => set({ activeView: view }),
  selectedProgramId: null,
  selectProgram: (id) => set({ selectedProgramId: id }),
  clearSelection: () => set({ selectedProgramId: null }),
}));

export type { ViewType, NavigationStore };
