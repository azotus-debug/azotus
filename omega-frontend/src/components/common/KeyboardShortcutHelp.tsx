"use client";

import React, { useEffect, useCallback } from "react";

interface ShortcutGroup {
  title: string;
  shortcuts: { keys: string[]; description: string }[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Playback",
    shortcuts: [
      { keys: ["K", "Space"], description: "Play / Pause" },
      { keys: ["Shift", "Space"], description: "Play current segment only" },
      { keys: ["J"], description: "Previous segment" },
      { keys: ["L"], description: "Next segment" },
      { keys: ["["], description: "Slower playback" },
      { keys: ["]"], description: "Faster playback" },
      { keys: ["\u2190"], description: "Frame step back" },
      { keys: ["\u2192"], description: "Frame step forward" },
      { keys: ["Home"], description: "Jump to first segment" },
      { keys: ["End"], description: "Jump to last segment" },
    ],
  },
  {
    title: "Editing",
    shortcuts: [
      { keys: ["\u2318", "S"], description: "Save" },
      { keys: ["\u2318", "Z"], description: "Undo" },
      { keys: ["\u2318", "F"], description: "Search segments" },
      { keys: ["\u2318", "H"], description: "Find & Replace" },
      { keys: ["\u2318", "G"], description: "Go to segment #" },
    ],
  },
  {
    title: "AI Editing",
    shortcuts: [
      { keys: ["Shift", "Enter"], description: "Get AI alternatives (in textarea)" },
      { keys: ["1", "2", "3"], description: "Select alternative" },
      { keys: ["Esc"], description: "Dismiss alternatives" },
    ],
  },
  {
    title: "Layout",
    shortcuts: [
      { keys: ["\u2318", "B"], description: "Toggle sidebar" },
      { keys: ["\u2318", "Shift", "V"], description: "Cycle video size" },
    ],
  },
  {
    title: "Segments",
    shortcuts: [
      { keys: ["Click"], description: "Select segment" },
      { keys: ["\u2318", "Click"], description: "Multi-select" },
      { keys: ["Merge"], description: "Select 2+ \u2192 merge" },
      { keys: ["Split"], description: "Select 1 \u2192 split at playhead" },
    ],
  },
  {
    title: "Graphic Zones",
    shortcuts: [
      { keys: ["G"], description: "Start/end graphic zone mark" },
    ],
  },
];

interface KeyboardShortcutHelpProps {
  open: boolean;
  onClose: () => void;
}

export function KeyboardShortcutHelp({ open, onClose }: KeyboardShortcutHelpProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (open) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [open, handleKeyDown]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0, 0, 0, 0.6)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="bg-[#1a1a2e] border border-[#333] rounded-xl max-w-lg w-full mx-4 p-6 shadow-2xl max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-white">Keyboard Shortcuts</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors p-1"
            aria-label="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-5">
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.title}>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                {group.title}
              </h3>
              <div className="space-y-1.5">
                {group.shortcuts.map((shortcut, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-white/5"
                  >
                    <span className="text-sm text-gray-300">{shortcut.description}</span>
                    <div className="flex items-center gap-1">
                      {shortcut.keys.map((key, j) => (
                        <React.Fragment key={j}>
                          {j > 0 && <span className="text-gray-600 text-xs">+</span>}
                          <kbd className="inline-flex items-center justify-center min-w-[24px] h-6 px-1.5 text-xs font-mono text-gray-300 bg-[#0d0d1a] border border-[#444] rounded shadow-sm">
                            {key}
                          </kbd>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-5 pt-4 border-t border-[#333] text-center">
          <span className="text-xs text-gray-500">
            Press <kbd className="px-1 py-0.5 text-[10px] font-mono bg-[#0d0d1a] border border-[#444] rounded">?</kbd> to toggle this help
          </span>
        </div>
      </div>
    </div>
  );
}

export default KeyboardShortcutHelp;
