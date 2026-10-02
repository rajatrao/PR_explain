import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiProxy = process.env.API_PROXY || "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": apiProxy,
    },
  },
});
