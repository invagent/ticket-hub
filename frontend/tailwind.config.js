/** @type {import('tailwindcss').Config} */
// `hub` namespace = design tokens from the 2026-07 console redesign
// (基准来源：反思诊断工作台). Kept under its own prefix so it never collides
// with Tailwind's built-in palettes still used by not-yet-reskinned pages
// (ticket/hub-issue detail, customers, login, admin catalog/skills).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        hub: {
          page: "#f6f4ef",
          panel: "#fbf9f5",
          card: "#ffffff",
          segment: "#ece7dc",
          border: "#e8e3d9",
          borderLight: "#efeae0",
          badgeNeutralBg: "#f3f0e9",
          controlBorder: "#c9c3b6",
          text: "#2b2a26",
          textSecondary: "#57524a",
          textMuted: "#8b8577",
          textFaint: "#a09a8c",
          // semantic four-piece sets: DEFAULT (main) / light / border / deep
          teal: { DEFAULT: "#177e83", light: "#e9f3f2", border: "#cfe4e2", deep: "#14666a" },
          rose: { DEFAULT: "#b04a4a", light: "#fbf1ef", border: "#eed7d2", deep: "#b04a4a" },
          amber: { DEFAULT: "#c98a1e", light: "#faf3e3", border: "#eddfba", deep: "#9a6c1c" },
          green: { DEFAULT: "#2f7d4f", light: "#edf5ee", border: "#bcd9c4", deep: "#2f7d4f" },
          purple: { DEFAULT: "#7a5ba6", light: "#f2edf8", border: "#ddd0ec", deep: "#7a5ba6" },
          cyan: { DEFAULT: "#2383a0", light: "#e7f2f6", border: "#c9e0e8", deep: "#2383a0" },
          blue: { DEFAULT: "#3d6bb3", light: "#eaf0f8", border: "#cfdcee", deep: "#3d6bb3" },
          emerald: { DEFAULT: "#1e8a63", light: "#e6f4ed", border: "#bfdccd", deep: "#1e8a63" },
          neutral: { DEFAULT: "#8b8577", light: "#f3f0e9", border: "#e8e3d9", deep: "#8b8577" },
        },
      },
      fontFamily: {
        hub: [
          "-apple-system",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
