import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// VITE_MOCK=1 liga o mock em memoria; sem a variavel o bloco vira codigo morto e o build nao o inclui.
const mock = process.env.VITE_MOCK ? '1' : ''

export default defineConfig({
  base: '/admin/',
  plugins: [react(), tailwindcss()],
  define: { 'import.meta.env.VITE_MOCK': JSON.stringify(mock) },
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5175 },
  preview: { port: 4175 },
})
