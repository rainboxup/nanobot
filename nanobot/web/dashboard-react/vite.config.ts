import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  const isProd = mode === 'production';
  const proxyTarget =
    env.NANOBOT_WEB_PROXY_TARGET || env.VITE_PROXY_TARGET || 'http://127.0.0.1:18790';
  return {
    base: isProd ? '/static/' : '/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR can be disabled via DISABLE_HMR env var.
      hmr: process.env.DISABLE_HMR !== 'true',
      proxy: {
        '/api': proxyTarget,
        '/ws': {
          target: proxyTarget,
          ws: true,
        },
      },
    },
    build: {
      outDir: path.resolve(__dirname, '../static'),
      emptyOutDir: true,
    },
  };
});
