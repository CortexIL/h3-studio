import '@fontsource-variable/inter'
import '@fontsource-variable/heebo'
import './styles/globals.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'

// After a deploy the old hashed chunks are gone. A tab opened before it would
// 404 on the next lazy route; reloading picks up the new build instead.
window.addEventListener('vite:preloadError', () => {
  window.location.reload()
})

// `npm run dev:mock`: serve the whole UI from fake data, with no backend and no
// account. Dead code in a production build, so it is dropped from the bundle.
if (import.meta.env.DEV && import.meta.env.VITE_MOCK_API === '1') {
  const { worker } = await import('./mocks/browser')
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true })
}

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
