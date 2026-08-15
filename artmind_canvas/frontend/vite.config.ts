import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the app on 5173 and proxies /api/* to the canvas backend
// (port 8380 — outside artmind's 8377/8378/8379 triad). Prod: `vite build`
// emits dist/, which the FastAPI backend mounts via StaticFiles.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8380",
        changeOrigin: true,
      },
    },
  },
});
