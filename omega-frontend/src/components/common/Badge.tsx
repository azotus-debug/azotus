"use client";

import React from "react";

type BadgeVariant = "default" | "info" | "success" | "warning" | "error" | "danger";

interface BadgeProps {
  label: string;
  variant?: BadgeVariant;
  className?: string;
}

export default function Badge({ label, variant = "info", className }: BadgeProps) {
  const classes = ["badge", `badge--${variant}`, className].filter(Boolean).join(" ");
  return <span className={classes}>{label}</span>;
}
