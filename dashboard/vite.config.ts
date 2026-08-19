import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    watch: process.env.CODEX_SANDBOX === "seatbelt" ? { useFsEvents: false, usePolling: true } : undefined,
  },
  build: { outDir: "dist", emptyOutDir: true },
});
