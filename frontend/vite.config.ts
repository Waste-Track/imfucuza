/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "Imfucuza",
        short_name: "Imfucuza",
        description: "Waste pickups and plastic for points in Madina",
        theme_color: "#1F6B45",
        background_color: "#F4F6F2",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
          { src: "icon-maskable.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        runtimeCaching: [
          {
            // Last known pickups, points and jobs stay readable offline. Writes
            // always go to the network.
            urlPattern: ({ url, request }) =>
              request.method === "GET" && url.pathname.startsWith("/v1/"),
            handler: "NetworkFirst",
            options: {
              cacheName: "api",
              networkTimeoutSeconds: 6,
              expiration: { maxEntries: 60, maxAgeSeconds: 7 * 24 * 60 * 60 },
            },
          },
          {
            urlPattern: ({ url }) =>
              url.origin === "https://fonts.googleapis.com" ||
              url.origin === "https://fonts.gstatic.com",
            handler: "StaleWhileRevalidate",
            options: { cacheName: "fonts" },
          },
        ],
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        // Vendor code changes rarely, so phones keep it cached across app updates.
        manualChunks: {
          react: ["react", "react-dom", "react-router"],
          supabase: ["@supabase/supabase-js"],
        },
      },
    },
  },
  server: {
    // Locally the Engine runs on :8000. The proxy keeps the app same-origin, so
    // no CORS setup is needed in development.
    proxy: { "/v1": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
