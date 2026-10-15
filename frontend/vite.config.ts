import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'prompt',
      injectRegister: false,
      includeAssets: [],
      manifest: {
        id: '/',
        name: 'MEDKIT · 우리집 약품함',
        short_name: 'MEDKIT',
        description: '보관 약품과 복약 기록을 확인하는 스마트 약품함',
        lang: 'ko',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        theme_color: '#206d5c',
        background_color: '#f5f7f4',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable',
          },
        ],
      },
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // 원본 SVG는 고정 파일명이라 별도 캐시 버전을 붙인다.
        dontCacheBustURLsMatching: /\/assets\/.*-[A-Za-z0-9_-]{8}\./,
      },
    }),
  ],
  build: { target: 'es2020' },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
});
