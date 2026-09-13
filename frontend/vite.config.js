import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // dev-time proxy so the browser never deals with CORS
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
