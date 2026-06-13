import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";

// During `npm run dev`, the UI runs on http://localhost:5173 and proxies
// API requests to the Flask backend on http://127.0.0.1:5050 so we avoid
// CORS quirks in dev. In production the same-origin setup means the
// proxy is not needed.
export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  server: {
    // Dedicated, locked port so a sibling Vite project (e.g. PCA Planner on
    // 5173) can't displace us. strictPort makes Vite fail loudly instead of
    // silently hopping to another port and breaking the /api proxy.
    port: 5180,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:5050", changeOrigin: true },
    },
  },
});
