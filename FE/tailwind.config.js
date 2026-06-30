/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  // ── Dark mode via .dark class on <html> ──────────
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // "brand" is now the seal red (son). Channel syntax so opacity
        // utilities (bg-brand/8, border-brand/40) work AND the value
        // flips automatically light↔dark via --brand-rgb.
        brand: {
          DEFAULT: "rgb(var(--brand-rgb) / <alpha-value>)",
          ink:     "rgb(var(--ink-rgb) / <alpha-value>)",
        },
        seal: "rgb(var(--brand-rgb) / <alpha-value>)",
        // Surface/text/border reference CSS variables so they flip
        // automatically when .dark is toggled on <html>.
        surface: {
          base:     "var(--bg-base)",
          sidebar:  "var(--bg-sidebar)",
          card:     "var(--bg-card)",
          elevated: "var(--bg-elevated)",
          panel:    "var(--bg-panel)",
          hover:    "var(--bg-hover)",
        },
        text: {
          primary:   "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted:     "var(--text-muted)",
          inverse:   "var(--text-inverse)",
        },
        slate: "var(--slate)",
        border: "var(--border-color)",
        "border-strong": "var(--border-strong)",
      },
      fontFamily: {
        // Three voices: scholarship (serif), instrument (sans), apparatus (mono)
        display: ["Spectral", "Georgia", "serif"],
        reading: ["Spectral", "Georgia", "serif"],
        body: ["Inter", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulse: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
      animation: {
        fadeUp: "fadeUp 450ms ease-out both",
        pulseSoft: "pulse 1.3s ease-in-out infinite",
      },
      boxShadow: {
        glow:         "0 0 0 2px rgba(178,58,46,0.18)",
        card:         "var(--shadow-card)",
        "card-hover": "var(--shadow-card-hover)",
        header:       "0 1px 0 var(--border-color)",
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
