import { setupServer } from 'msw/node'

// Each test adds the handlers it needs with server.use(...).
export const server = setupServer()
