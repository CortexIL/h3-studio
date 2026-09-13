import { Server, WifiOff } from 'lucide-react'

import { useAdminRuns } from '@/api/queries'
import { Elapsed } from '@/components/app/Elapsed'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtDuration, fmtRelative, fmtUsd, fmtWhen } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useT } from '@/i18n'

import { Panel } from './Panel'

export function RunsTab() {
  const t = useT()
  const runs = useAdminRuns()
  const list = runs.data?.runs ?? []
  return (
    <Panel
      title={t('admin.sessions')}
      description={t('admin.sessionsDesc')}
      actions={
        runs.data ? (
          <div className="shrink-0 text-end">
            <p className="text-2xs text-muted-foreground">{t('admin.total')}</p>
            <p className="font-semibold tabular-nums">{fmtUsd(runs.data.total_cost_usd)}</p>
          </div>
        ) : null
      }
      className="p-0 [&>header]:p-5 [&>header]:pb-0"
    >
      {runs.isPending ? (
        <div className="grid gap-2 p-5" aria-label={t('admin.loadingSessions')}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : !runs.data ? (
        <EmptyState
          icon={WifiOff}
          title={t('admin.loadSessionsFailed')}
          action={
            <Button size="sm" variant="outline" onClick={() => void runs.refetch()}>
              {t('common.tryAgain')}
            </Button>
          }
        />
      ) : list.length === 0 ? (
        <EmptyState icon={Server} title={t('admin.noSessions')} description={t('admin.noSessionsDesc')} />
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="ps-5">{t('admin.col.started')}</TableHead>
              <TableHead>{t('admin.col.gpu')}</TableHead>
              <TableHead>{t('admin.col.state')}</TableHead>
              <TableHead className="text-end">{t('admin.col.duration')}</TableHead>
              <TableHead className="text-end">{t('admin.col.cost')}</TableHead>
              <TableHead className="pe-5">{t('admin.col.why')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((run) => {
              const live = run.ended_at === null
              return (
                <TableRow key={run.id}>
                  <TableCell className="ps-5">
                    {run.started_at ? <time title={fmtWhen(run.started_at)}>{fmtRelative(run.started_at)}</time> : '—'}
                  </TableCell>
                  <TableCell>{run.gpu_type ?? '—'}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5" title={run.status}>
                      <span className={cn('size-1.5 rounded-full', live ? 'animate-pulse bg-info' : 'bg-faint')} />
                      {t(live ? 'admin.running' : 'admin.ended')}
                    </span>
                  </TableCell>
                  <TableCell className="text-end tabular-nums">
                    {run.started_at === null
                      ? '—'
                      : live
                        ? <Elapsed since={run.started_at} />
                        : fmtDuration((run.ended_at ?? run.started_at) - run.started_at)}
                  </TableCell>
                  <TableCell className="text-end tabular-nums">{fmtUsd(run.cost_estimate)}</TableCell>
                  <TableCell className="pe-5 text-muted-foreground">{live ? t('admin.stillRunning') : run.note ?? '—'}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}
