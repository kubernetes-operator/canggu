import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 운영 빌드는 base=/operating/ 로 자산 URL 을 prefix 한다. gateway 가 prefix strip 후
// hub 가 루트에서 서빙하므로 자산이 정상 로드된다.
// dev 는 base=/ 로 두어 프록시(/api, /live)가 그대로 동작한다.
export default defineConfig(({ command }) => ({
  base: command === "build" ? process.env.CANGGU_BASE_PATH || "/operating/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/live": { target: "ws://localhost:8000", ws: true },
    },
  },
}));
