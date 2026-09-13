import { createBrowserRouter } from 'react-router'

import { AppShell } from '@/components/app/AppShell'
import { AccountPage } from '@/features/account/AccountPage'
import { ArchivePage } from '@/features/archive/ArchivePage'
import { LoginPage } from '@/features/auth/LoginPage'
import { BetaPage } from '@/features/beta/BetaPage'
import { NotFound } from '@/features/errors/NotFound'
import { StudioPage } from '@/features/studio/StudioPage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <StudioPage /> },
      { path: 'archive', element: <ArchivePage /> },
      { path: 'account', element: <AccountPage /> },
      { path: 'beta', element: <BetaPage /> },
      // Only admins ever load this chunk.
      { path: 'admin', lazy: async () => ({ Component: (await import('@/features/admin/AdminPage')).AdminPage }) },
      { path: '*', element: <NotFound /> },
    ],
  },
])
