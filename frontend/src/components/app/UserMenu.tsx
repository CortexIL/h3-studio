import { ChevronDown, LogOut, ShieldCheck, UserRound } from 'lucide-react'
import { Link } from 'react-router'

import { useSignOut } from '@/api/mutations'
import { useMe } from '@/api/queries'
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

export function UserMenu() {
  const { data: me } = useMe()
  const signOut = useSignOut()
  if (!me) return <Skeleton className="h-8 w-24 rounded-full" />
  const isAdmin = me.role === 'admin'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="shrink-0 gap-2 rounded-full pr-2 pl-1" aria-label="Account menu">
          <span className="grid size-6 place-items-center rounded-full bg-primary text-2xs font-semibold text-primary-foreground uppercase">
            {me.email.charAt(0)}
          </span>
          <span className="hidden max-w-44 truncate md:inline">{me.email}</span>
          <ChevronDown className="size-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="grid gap-0.5 font-normal">
          <span className="truncate text-sm">{me.email}</span>
          <span className="text-2xs text-muted-foreground">{isAdmin ? 'Administrator' : 'Member'}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/account">
            <UserRound /> Account
          </Link>
        </DropdownMenuItem>
        {isAdmin ? (
          <DropdownMenuItem asChild>
            <Link to="/admin">
              <ShieldCheck /> Admin
            </Link>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => signOut.mutate()} disabled={signOut.isPending}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
