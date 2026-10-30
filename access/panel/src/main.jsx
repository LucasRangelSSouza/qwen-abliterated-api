import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './App.jsx'

async function start() {
  if (import.meta.env.VITE_MOCK) {
    const { installMock } = await import('./lib/mock.js')
    installMock()
  }
  createRoot(document.getElementById('root')).render(<App />)
}
start()
