import { defineConfig } from "vite";
import { writeFileSync } from "node:fs";
export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [
    {
      name: "offline-index",
      closeBundle() {
        writeFileSync(
          "../qgis_plugin/ftth_demo0/web/index.html",
          `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' qrc:; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><link rel="stylesheet" href="ftth-ui.css"><title>FTTH</title></head><body><div id="root"></div><script src="qrc:///qtwebchannel/qwebchannel.js"></script><script src="app.js"></script></body></html>`,
        );
      },
    },
  ],
  base: "./",
  build: {
    target: "chrome87",
    cssTarget: "chrome87",
    outDir: "../qgis_plugin/ftth_demo0/web",
    emptyOutDir: true,
    lib: {
      entry: "src/main.tsx",
      name: "FTTH",
      formats: ["iife"],
      fileName: () => "app.js",
    },
    rollupOptions: { output: { assetFileNames: "[name][extname]" } },
  },
  server: { host: "127.0.0.1" },
});
