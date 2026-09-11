import '@fontsource-variable/inter'
import './styles/globals.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'

// After a deploy the old hashed chunks are gone. A tab opened before it would
// 404 on the next lazy route; reloading picks up the new build instead.
window.addEventListener('vite:preloadError', () => {
  window.location.reload()
})

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
