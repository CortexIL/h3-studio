import { ChevronDown, LogOut, ShieldCheck, UserRound } from 'lucide-react'
import { Link } from 'react-router'

import { useSignOut } from '@/api/mutations'
import { useMe } from '@/api/queries'
import { Avatar } from '@/components/app/Avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { useT } from '@/i18n'

export function UserMenu() {
  const { data: me } = useMe()
  const signOut = useSignOut()
  const t = useT()
  if (!me) return <Skeleton className="h-8 w-24 rounded-full" />
  const isAdmin = me.role === 'admin'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="shrink-0 gap-2 rounded-full pe-2 ps-1" aria-label={t('user.menu')}>
          <Avatar email={me.email} url={me.avatar_url} size="xs" />
          <span className="hidden max-w-44 truncate md:inline">{me.email}</span>
          <ChevronDown className="size-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="grid gap-0.5 font-normal">
          <span className="truncate text-sm">{me.email}</span>
          <span className="text-2xs text-muted-foreground">{t(isAdmin ? 'user.administrator' : 'user.member')}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/account">
            <UserRound /> {t('user.account')}
          </Link>
        </DropdownMenuItem>
        {isAdmin ? (
          <DropdownMenuItem asChild>
            <Link to="/admin">
              <ShieldCheck /> {t('nav.admin')}
            </Link>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => signOut.mutate()} disabled={signOut.isPending}>
          <LogOut /> {t('user.signOut')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
