"use client";

import React, { useEffect, useState } from "react";
import { useToastStore, type Toast } from "@/store/toast";

const variantStyles: Record<string, { border: string; icon: string }> = {
  success: { border: "border-l-green-500", icon: "text-green-400" },
  error: { border: "border-l-red-500", icon: "text-red-400" },
  info: { border: "border-l-blue-500", icon: "text-blue-400" },
};

function ToastItem({ toast }: { toast: Toast }) {
  const removeToast = useToastStore((s) => s.removeToast);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Trigger enter animation on next frame
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const handleClose = () => {
    setVisible(false);
    // Wait for exit animation before removing from store
    setTimeout(() => removeToast(toast.id), 200);
  };

  const styles = variantStyles[toast.variant] ?? variantStyles.info;

  return (
    <div
      className={`
        flex items-start gap-3 w-80 px-4 py-3
        bg-[#1a1a2e] border-l-4 ${styles.border}
        rounded-md shadow-lg shadow-black/30
        transition-all duration-200 ease-in-out
        ${visible ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0"}
      `}
      role="alert"
    >
      <p className="flex-1 text-sm text-white leading-snug">{toast.message}</p>
      <button
        onClick={handleClose}
        className="shrink-0 text-gray-400 hover:text-white transition-colors"
        aria-label="Dismiss notification"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="h-4 w-4"
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
            clipRule="evenodd"
          />
        </svg>
      </button>
    </div>
  );
}

export default function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col-reverse gap-3 pointer-events-auto">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  );
}
