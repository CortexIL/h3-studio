import { ShieldAlert } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'

import { useMe } from '@/api/queries'
import { EmptyState } from '@/components/app/EmptyState'
import { Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useDocumentTitle } from '@/lib/hooks'

import { GpuTab } from './GpuTab'
import { RunsTab } from './RunsTab'
import { UsersTab } from './UsersTab'

const TABS = ['gpu', 'users', 'runs'] as const
type Tab = (typeof TABS)[number]
const isTab = (value: string | null): value is Tab => TABS.includes(value as Tab)

export function AdminPage() {
  useDocumentTitle('Admin')
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: Tab = isTab(raw) ? raw : 'gpu'

  // The server already refuses /admin to non-admins; this covers a role that
  // changed while the tab was open.
  if (me.isPending) {
    return (
      <Page title="Admin" width="wide">
        <Skeleton className="h-64 w-full rounded-xl" />
      </Page>
    )
  }
  if (me.data?.role !== 'admin') {
    return (
      <Page title="Admin" width="wide">
        <EmptyState
          icon={ShieldAlert}
          title="Admins only"
          description="Ask an admin if something here needs changing."
          action={
            <Button asChild size="sm" variant="outline">
              <Link to="/">Back to the Studio</Link>
            </Button>
          }
        />
      </Page>
    )
  }

  return (
    <Page title="Admin" description="The shared GPU, everyone's accounts, and what it has cost." width="wide">
      <Tabs
        value={tab}
        onValueChange={(value) =>
          setParams(
            (prev) => {
              const next = new URLSearchParams(prev)
              if (value === 'gpu') next.delete('tab')
              else next.set('tab', value)
              return next
            },
            { replace: true },
          )
        }
      >
        <TabsList>
          <TabsTrigger value="gpu">GPU</TabsTrigger>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="runs">Runs</TabsTrigger>
        </TabsList>
        <TabsContent value="gpu" className="mt-4">
          <GpuTab />
        </TabsContent>
        <TabsContent value="users" className="mt-4">
          <UsersTab />
        </TabsContent>
        <TabsContent value="runs" className="mt-4">
          <RunsTab />
        </TabsContent>
      </Tabs>
    </Page>
  )
}
