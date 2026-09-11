import { createBrowserRouter } from 'react-router'

import { AppShell } from '@/components/app/AppShell'
import { AccountPage } from '@/features/account/AccountPage'
import { LoginPage } from '@/features/auth/LoginPage'
import { NotFound, Placeholder } from '@/features/placeholder/Placeholder'
import { StudioPage } from '@/features/studio/StudioPage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <StudioPage /> },
      { path: 'archive', element: <Placeholder title="Archive" /> },
      { path: 'account', element: <AccountPage /> },
      { path: 'admin', element: <Placeholder title="Admin" /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])
