import { defineConfig } from "vite";

export default defineConfig({
  base: "/work/",
  build: {
    emptyOutDir: true,
    outDir: "../../src/agent_commons/ui/static/work",
    rollupOptions: {
      output: {
        assetFileNames: "assets/work-[hash][extname]",
        entryFileNames: "assets/work-[hash].js"
      }
    }
  }
});
