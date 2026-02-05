import { useEffect } from "react";

interface ShortcutOptions {
  enabled: boolean;
  onAccept: () => void;
  onEdit: () => void;
  onSkip: () => void;
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || target.isContentEditable;
}

export function useKeyboardShortcuts({ enabled, onAccept, onEdit, onSkip }: ShortcutOptions) {
  useEffect(() => {
    if (!enabled) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target)) return;

      switch (event.key) {
        case "Enter":
          event.preventDefault();
          onAccept();
          break;
        case "ArrowRight":
          event.preventDefault();
          onSkip();
          break;
        case "e":
        case "E":
          event.preventDefault();
          onEdit();
          break;
        default:
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [enabled, onAccept, onEdit, onSkip]);
}
