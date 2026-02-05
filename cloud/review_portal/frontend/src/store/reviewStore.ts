import { create } from "zustand";
import type { ReviewData, Issue, ReviewAction } from "../types";

interface ReviewState {
  data: ReviewData | null;
  currentIssueIndex: number;
  resolvedIssues: Set<string>;
  isLoading: boolean;
  error: string | null;

  setData: (data: ReviewData) => void;
  setLoading: (loading: boolean) => void;
  setError: (message: string | null) => void;
  getCurrentIssue: () => Issue | null;
  resolveIssue: (action: ReviewAction, newText?: string) => void;
  nextIssue: () => void;
  prevIssue: () => void;
  isComplete: () => boolean;
}

export const useReviewStore = create<ReviewState>((set, get) => ({
  data: null,
  currentIssueIndex: 0,
  resolvedIssues: new Set(),
  isLoading: false,
  error: null,

  setData: (data) => set({ data, currentIssueIndex: 0, resolvedIssues: new Set() }),
  setLoading: (loading) => set({ isLoading: loading }),
  setError: (message) => set({ error: message }),

  getCurrentIssue: () => {
    const { data, currentIssueIndex } = get();
    if (!data || currentIssueIndex >= data.issues.length) return null;
    return data.issues[currentIssueIndex];
  },

  resolveIssue: (action, newText) => {
    const { data, currentIssueIndex, resolvedIssues } = get();
    if (!data) return;

    const issue = data.issues[currentIssueIndex];
    if (!issue) return;

    const nextIssues = data.issues.map((item, index) => {
      if (index !== currentIssueIndex) return item;
      if (action === "skip") return item;
      if (!newText) return item;
      return { ...item, current_text: newText };
    });

    const newResolved = new Set(resolvedIssues);
    newResolved.add(issue.id);

    set({
      data: { ...data, issues: nextIssues },
      resolvedIssues: newResolved,
      currentIssueIndex: Math.min(currentIssueIndex + 1, data.issues.length),
    });

    // TODO: Send action to backend when API is ready.
  },

  nextIssue: () => {
    const { data, currentIssueIndex } = get();
    if (!data) return;
    if (currentIssueIndex < data.issues.length - 1) {
      set({ currentIssueIndex: currentIssueIndex + 1 });
    }
  },

  prevIssue: () => {
    const { currentIssueIndex } = get();
    if (currentIssueIndex > 0) {
      set({ currentIssueIndex: currentIssueIndex - 1 });
    }
  },

  isComplete: () => {
    const { data, resolvedIssues } = get();
    if (!data) return false;
    return resolvedIssues.size >= data.issues.length;
  },
}));
