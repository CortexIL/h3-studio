import { NavLink, Outlet, Link } from 'react-router'

import { useMe, useStatus } from '@/api/queries'
import logo from '@/assets/logo.png'
import { Badge } from '@/components/ui/badge'
import { ClipViewerDialog } from '@/features/viewer/ClipViewerDialog'
import { cn } from '@/lib/utils'
import { useT } from '@/i18n'

import { AppearanceToggles } from './AppearanceToggles'
import { PodStatus } from './PodStatus'
import { UserMenu } from './UserMenu'

function NavItem({ to, end, children }: { to: string; end?: boolean; children: string }) {
  return (
    <NavLink
      to={to}
      end={end ?? false}
      className={({ isActive }) =>
        cn(
          'shrink-0 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors sm:px-3',
          isActive ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
        )
      }
    >
      {children}
    </NavLink>
  )
}

function ServerNotice() {
  const { data } = useStatus()
  if (!data?.notice) return null
  return (
    <div role="status" className="border-b border-info/25 bg-info/10 px-4 py-2 text-center text-sm text-info">
      {data.notice}
    </div>
  )
}

export function AppShell() {
  const t = useT()
  const { data: me } = useMe()
  const { data: status } = useStatus()
  return (
    // A page marked data-fill-viewport (the Studio) gets exactly the window on
    // wide screens and scrolls inside its own panels; every other page scrolls
    // the document as usual.
    <div className="flex min-h-dvh flex-col lg:has-[[data-fill-viewport]]:h-dvh">
      <header className="sticky top-0 z-30 flex h-13 shrink-0 items-center gap-2 border-b bg-card/85 px-3 backdrop-blur sm:gap-4 sm:px-4">
        <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md font-semibold" aria-label={t('nav.home')}>
          <img src={logo} alt="" className="size-7 max-w-none shrink-0 rounded-md" />
          <span className="hidden sm:inline">H3 Studio</span>
        </Link>
        <nav aria-label={t('nav.main')} className="flex min-w-0 items-center gap-1 overflow-x-auto">
          <NavItem to="/" end>
            {t('nav.studio')}
          </NavItem>
          <NavItem to="/archive">{t('nav.archive')}</NavItem>
          <NavItem to="/beta">{t('nav.beta')}</NavItem>
          {me?.role === 'admin' ? <NavItem to="/admin">{t('nav.admin')}</NavItem> : null}
        </nav>
        <div className="flex-1" />
        {status?.config.mock ? (
          <Badge variant="outline" className="hidden border-warn/40 text-warn sm:inline-flex">
            {t('shell.demo')}
          </Badge>
        ) : null}
        <AppearanceToggles className="hidden items-center gap-0.5 sm:flex" withLayout />
        <PodStatus />
        <UserMenu />
      </header>
      <ServerNotice />
      <main className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
      <ClipViewerDialog />
    </div>
  )
}
