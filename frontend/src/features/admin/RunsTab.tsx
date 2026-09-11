import { Server, WifiOff } from 'lucide-react'

import { useAdminRuns } from '@/api/queries'
import { Elapsed } from '@/components/app/Elapsed'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtDuration, fmtRelative, fmtUsd, fmtWhen } from '@/lib/format'
import { cn } from '@/lib/utils'

import { Panel } from './Panel'

export function RunsTab() {
  const runs = useAdminRuns()
  const list = runs.data?.runs ?? []
  return (
    <Panel
      title="GPU sessions"
      description="Every rented session and what it cost."
      actions={
        runs.data ? (
          <div className="shrink-0 text-right">
            <p className="text-2xs text-muted-foreground">Total</p>
            <p className="font-semibold tabular-nums">{fmtUsd(runs.data.total_cost_usd)}</p>
          </div>
        ) : null
      }
      className="p-0 [&>header]:p-5 [&>header]:pb-0"
    >
      {runs.isPending ? (
        <div className="grid gap-2 p-5" aria-label="Loading sessions">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : !runs.data ? (
        <EmptyState
          icon={WifiOff}
          title="Couldn't load the sessions"
          action={
            <Button size="sm" variant="outline" onClick={() => void runs.refetch()}>
              Try again
            </Button>
          }
        />
      ) : list.length === 0 ? (
        <EmptyState icon={Server} title="No GPU sessions yet" description="One appears here the first time a GPU is rented." />
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">Started</TableHead>
              <TableHead>GPU</TableHead>
              <TableHead>State</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="pr-5">Why it stopped</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((run) => {
              const live = run.ended_at === null
              return (
                <TableRow key={run.id}>
                  <TableCell className="pl-5">
                    {run.started_at ? <time title={fmtWhen(run.started_at)}>{fmtRelative(run.started_at)}</time> : '—'}
                  </TableCell>
                  <TableCell>{run.gpu_type ?? '—'}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5" title={run.status}>
                      <span className={cn('size-1.5 rounded-full', live ? 'animate-pulse bg-info' : 'bg-faint')} />
                      {live ? 'Running' : 'Ended'}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {run.started_at === null
                      ? '—'
                      : live
                        ? <Elapsed since={run.started_at} />
                        : fmtDuration((run.ended_at ?? run.started_at) - run.started_at)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd(run.cost_estimate)}</TableCell>
                  <TableCell className="pr-5 text-muted-foreground">{live ? 'Still running' : run.note ?? '—'}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Panel>
  )
}
