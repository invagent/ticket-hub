import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  // Serve from sub-path when deployed (e.g. https://yjcj.online/ticket-hub/).
  // VITE_PUBLIC_BASE controls the base path for static asset URLs.
  // VITE_API_BASE (read in src/api/client.ts) controls API call prefix.
  base: process.env.VITE_PUBLIC_BASE || "/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/health": "http://localhost:8080",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
