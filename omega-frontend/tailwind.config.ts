import type { Config } from "tailwindcss";

const config: Config = {
    content: [
        "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
        "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
        "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ["var(--font-omega-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
                mono: ["var(--font-omega-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
            },
            colors: {
                background: "var(--background)",
                foreground: "var(--foreground)",
                // Omega Pro - Cinema Interface Palette
                omega: {
                    base: "#0b0f14",      // Deep void
                    panel: "#121a23",     // Main panel surface
                    surface: "#1b2632",   // Hover/Input layers
                    border: "#273445",    // Subtle dividers
                    primary: "#4cc9f0",   // Zero-gravity cyan
                    accent: "#f7b267",    // Warm signal for AI/alerts
                    success: "#22c55e",   // Green (Status)
                    alert: "#ef4444",     // Red (Destructive)
                    text: {
                        primary: "#e6edf3", // High legibility
                        secondary: "#9fb0c4", // Metadata
                        muted: "#5d7086",   // Disabled/Subtle
                    }
                }
            },
        },
    },
    plugins: [require("tailwindcss-animate")],
};
export default config;
