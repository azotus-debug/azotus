import { create } from "zustand";

type ViewType = "dashboard" | "programs" | "settings";

interface NavigationStore {
  activeView: ViewType;
  setActiveView: (view: ViewType) => void;
  selectedProgramId: string | null;
  selectProgram: (id: string) => void;
  clearSelection: () => void;
}

export const useNavigation = create<NavigationStore>((set) => ({
  activeView: "dashboard",
  setActiveView: (view) => set({ activeView: view }),
  selectedProgramId: null,
  selectProgram: (id) => set({ selectedProgramId: id }),
  clearSelection: () => set({ selectedProgramId: null }),
}));

export type { ViewType, NavigationStore };
