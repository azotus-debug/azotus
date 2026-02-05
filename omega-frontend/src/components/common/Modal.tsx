"use client";

import React from "react";

interface ModalProps {
  children: React.ReactNode;
  onClose?: () => void;
  overlayClassName?: string;
  panelClassName?: string;
}

export default function Modal({ children, onClose, overlayClassName, panelClassName }: ModalProps) {
  const overlayClasses = ["modal-overlay", overlayClassName].filter(Boolean).join(" ");
  const panelClasses = ["modal-panel", panelClassName].filter(Boolean).join(" ");

  return (
    <div className={overlayClasses} onClick={onClose} role="dialog" aria-modal="true">
      <div className={panelClasses} onClick={(event) => event.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}
