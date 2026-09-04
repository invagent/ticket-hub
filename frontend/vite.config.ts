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
      // 代理到远程 SIT 后端，本地无需启动 backend
      "/api": {
        target: "http://43.139.250.182",
        changeOrigin: true,
        rewrite: (path: string) => "/hub-issue" + path,
      },
      "/health": {
        target: "http://43.139.250.182",
        changeOrigin: true,
        rewrite: (path: string) => "/hub-issue" + path,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
