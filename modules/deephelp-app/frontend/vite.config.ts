import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: "built",
    manifest: "build-manifest.json",
    chunkSizeWarningLimit: 1100,
  },
  server: {
    proxy: Object.fromEntries(
      [
        "/auth",
        "/converse",
        "/history",
        "/business",
        "/debug",
        "/learning",
        "/observability",
      ].map((p) => [p, "http://127.0.0.1:8000"]),
    ),
  },
});
