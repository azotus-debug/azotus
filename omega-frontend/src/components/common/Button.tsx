"use client";

import React from "react";

type ButtonVariant = "primary" | "secondary" | "ghost";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export default function Button({
  variant = "primary",
  className,
  type = "button",
  ...props
}: ButtonProps) {
  const variantClass = `omega-button--${variant}`;
  const classes = ["omega-button", variantClass, className].filter(Boolean).join(" ");

  return <button className={classes} type={type} {...props} />;
}
