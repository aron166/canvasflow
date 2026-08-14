import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: "#070A11",
          900: "#0B0F19", // canvas ground
          800: "#111726",
          700: "#182034",
        },
      },
      boxShadow: {
        glow: "0 0 0 1px rgb(99 102 241 / 0.20), 0 12px 40px -12px rgb(79 70 229 / 0.45)",
        "glow-strong":
          "0 0 0 1px rgb(129 140 248 / 0.45), 0 0 32px -4px rgb(99 102 241 / 0.55)",
      },
      keyframes: {
        "pulse-ring": {
          "0%,100%": { opacity: "0.35" },
          "50%": { opacity: "0.9" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px) scale(0.99)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "pulse-ring": "pulse-ring 2s ease-in-out infinite",
        "fade-up": "fade-up 180ms cubic-bezier(0.16, 1, 0.3, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
