import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDirectory = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: resolve(currentDirectory),
  base: "/assets/app/",
  plugins: [react()],
  build: {
    outDir: resolve(currentDirectory, "../src/muxdev/api/static/app"),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api": "http://127.0.0.1:18977",
      "/health": "http://127.0.0.1:18977",
    },
  },
});
