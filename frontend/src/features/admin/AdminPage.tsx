import { ShieldAlert } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'

import { useMe } from '@/api/queries'
import { EmptyState } from '@/components/app/EmptyState'
import { Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useDocumentTitle } from '@/lib/hooks'
import { useT } from '@/i18n'

import { GpuTab } from './GpuTab'
import { RunsTab } from './RunsTab'
import { UsersTab } from './UsersTab'

const TABS = ['gpu', 'users', 'runs'] as const
type Tab = (typeof TABS)[number]
const isTab = (value: string | null): value is Tab => TABS.includes(value as Tab)

export function AdminPage() {
  const t = useT()
  useDocumentTitle(t('admin.docTitle'))
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: Tab = isTab(raw) ? raw : 'gpu'

  // The server already refuses /admin to non-admins; this covers a role that
  // changed while the tab was open.
  if (me.isPending) {
    return (
      <Page title={t('admin.title')} width="wide">
        <Skeleton className="h-64 w-full rounded-xl" />
      </Page>
    )
  }
  if (me.data?.role !== 'admin') {
    return (
      <Page title={t('admin.title')} width="wide">
        <EmptyState
          icon={ShieldAlert}
          title={t('admin.adminsOnly')}
          description={t('admin.adminsOnlyDesc')}
          action={
            <Button asChild size="sm" variant="outline">
              <Link to="/">{t('admin.back')}</Link>
            </Button>
          }
        />
      </Page>
    )
  }

  return (
    <Page title={t('admin.title')} description={t('admin.desc')} width="wide">
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
          <TabsTrigger value="gpu">{t('admin.tab.gpu')}</TabsTrigger>
          <TabsTrigger value="users">{t('admin.tab.users')}</TabsTrigger>
          <TabsTrigger value="runs">{t('admin.tab.runs')}</TabsTrigger>
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
