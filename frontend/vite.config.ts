import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке запросы к API проксируются на FastAPI (python -m aralshield.cli serve).
const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": api, "/layers": api, "/figures": api, "/docs": api, "/openapi.json": api },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
