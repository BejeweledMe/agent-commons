import { defineConfig } from "vite";

export default defineConfig({
  base: "/gallery/",
  build: {
    emptyOutDir: true,
    outDir: "../../src/agent_commons/ui/static/gallery",
    rollupOptions: {
      output: {
        assetFileNames: "assets/gallery-[hash][extname]",
        entryFileNames: "assets/gallery-[hash].js"
      }
    }
  }
});
