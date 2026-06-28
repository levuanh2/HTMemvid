/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  // ── Dark mode via .dark class on <html> ──────────
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#4f46e5",
          light: "#6366f1",
          pink: "#ec4899",
        },
        // All surface/text/border colors reference CSS variables
        // so they flip automatically when .dark is toggled on <html>
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
        border: "var(--border-color)",
        "border-strong": "var(--border-strong)",
      },
      fontFamily: {
        display: ["Sora", "Plus Jakarta Sans", "Inter", "system-ui", "sans-serif"],
        body: ["DM Sans", "Inter", "system-ui", "sans-serif"],
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        pulse: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
        bounceUp: {
          "0%, 80%, 100%": { transform: "translateY(0)" },
          "40%": { transform: "translateY(-6px)" },
        },
      },
      animation: {
        fadeUp: "fadeUp 450ms ease-out both",
        shimmer: "shimmer 1.6s linear infinite",
        pulseSoft: "pulse 1.3s ease-in-out infinite",
        bounce: "bounceUp 1.2s ease-in-out infinite",
      },
      boxShadow: {
        glow:          "0 0 20px rgba(79,70,229,0.14)",
        glowStrong:    "0 0 28px rgba(79,70,229,0.20)",
        card:          "var(--shadow-card)",
        "card-hover":  "var(--shadow-card-hover)",
        "bubble-ai":   "0 1px 4px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04)",
        header:        "0 1px 0 rgba(0,0,0,0.08)",
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
