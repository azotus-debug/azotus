/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        "review-bg": "#0a0a0a",
        "review-card": "#141414",
        "review-border": "#2a2a2a",
        "review-accent": "#4ade80",
        "review-warning": "#fbbf24",
        "review-error": "#ef4444",
      },
      fontFamily: {
        display: ["Newsreader", "serif"],
        body: ["Space Grotesk", "Noto Sans", "sans-serif"],
      },
    },
  },
  plugins: [],
}
