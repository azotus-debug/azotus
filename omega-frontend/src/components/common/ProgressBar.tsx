"use client";

import React from "react";

interface ProgressBarProps {
  value: number;
  label?: React.ReactNode;
}

export default function ProgressBar({ value, label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const ariaLabel = typeof label === 'string' ? label : 'Progress';

  return (
    <div className="progress-bar" aria-label={ariaLabel} role="progressbar" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}
