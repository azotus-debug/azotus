"use client";

import React from "react";

interface SkeletonProps {
  className?: string;
  variant?: "text" | "circular" | "rectangular" | "card";
  width?: string | number;
  height?: string | number;
  count?: number;
}

function SkeletonBase({ className = "", width, height, style }: {
  className?: string;
  width?: string | number;
  height?: string | number;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={`animate-pulse bg-white/[0.06] rounded ${className}`}
      style={{
        width: typeof width === "number" ? `${width}px` : width,
        height: typeof height === "number" ? `${height}px` : height,
        ...style,
      }}
    />
  );
}

export function Skeleton({ className = "", variant = "text", width, height, count = 1 }: SkeletonProps) {
  const items = Array.from({ length: count }, (_, i) => i);

  if (variant === "card") {
    return (
      <>
        {items.map((i) => (
          <div key={i} className={`rounded-xl border border-[#1e1e2e] bg-[#12121a] p-4 space-y-3 ${className}`}>
            <SkeletonBase height={140} className="rounded-lg w-full" />
            <SkeletonBase height={16} width="70%" />
            <SkeletonBase height={12} width="50%" />
            <div className="flex gap-2 pt-1">
              <SkeletonBase height={20} width={48} className="rounded-full" />
              <SkeletonBase height={20} width={48} className="rounded-full" />
            </div>
          </div>
        ))}
      </>
    );
  }

  if (variant === "circular") {
    return (
      <>
        {items.map((i) => (
          <SkeletonBase
            key={i}
            className={`rounded-full ${className}`}
            width={width || 40}
            height={height || 40}
          />
        ))}
      </>
    );
  }

  if (variant === "rectangular") {
    return (
      <>
        {items.map((i) => (
          <SkeletonBase
            key={i}
            className={className}
            width={width || "100%"}
            height={height || 100}
          />
        ))}
      </>
    );
  }

  // Default: text lines
  return (
    <div className={`space-y-2 ${className}`}>
      {items.map((i) => (
        <SkeletonBase
          key={i}
          height={height || 14}
          width={i === items.length - 1 ? "60%" : width || "100%"}
        />
      ))}
    </div>
  );
}

// Pre-built skeleton layouts
export function ProgramCardSkeleton() {
  return <Skeleton variant="card" />;
}

export function ProgramGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="program-grid">
      {Array.from({ length: count }, (_, i) => (
        <ProgramCardSkeleton key={i} />
      ))}
    </div>
  );
}

export function TrackRowSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-white/[0.02]">
          <SkeletonBase width={16} height={16} className="rounded" />
          <div className="flex-1 space-y-1.5">
            <SkeletonBase height={14} width="60%" />
            <SkeletonBase height={10} width="30%" />
          </div>
          <SkeletonBase height={24} width={64} className="rounded-full" />
          <SkeletonBase height={6} width={80} className="rounded-full" />
        </div>
      ))}
    </div>
  );
}

export function SummaryCardsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="ops-summary-grid">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="ops-summary-card">
          <SkeletonBase height={32} width={48} />
          <SkeletonBase height={12} width={80} style={{ marginTop: 8 }} />
        </div>
      ))}
    </div>
  );
}

export default Skeleton;
