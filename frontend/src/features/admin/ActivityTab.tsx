import { Users, WifiOff } from 'lucide-react'

import { useAdminActivity, useMe } from '@/api/queries'
import type { ActivityPerson } from '@/api/types'
import { Avatar } from '@/components/app/Avatar'
import { EmptyState } from '@/components/app/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtDuration, fmtUsd } from '@/lib/format'
import { cn } from '@/lib/utils'
import { tn, useT } from '@/i18n'

import { Callout, Panel } from './Panel'

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'warn' | 'ok' }) {
  return (
    <div className="rounded-lg border bg-background/40 px-3 py-2">
      <p className="text-2xs text-muted-foreground">{label}</p>
      <p className={cn('text-lg font-semibold tabular-nums',
                       tone === 'warn' && 'text-warn', tone === 'ok' && 'text-ok')}>
        {value}
      </p>
    </div>
  )
}

/** The one line an admin came here for: is this GPU somebody's work, or was it left on? */
function Verdict({ data }: { data: NonNullable<ReturnType<typeof useAdminActivity>['data']> }) {
  const t = useT()
  if (data.verdict === 'off') {
    return <Callout tone="info" title={t('admin.now.offTitle')}>{t('admin.now.offDesc')}</Callout>
  }
  if (data.verdict === 'working') {
    return (
      <Callout tone="info" title={tn('admin.now.workingOne', 'admin.now.workingMany', data.pods_up)}>
        {t('admin.now.workingDesc', { queued: data.queued, running: data.running })}
      </Callout>
    )
  }
  // Up, and nothing to render. Said as a sentence rather than three numbers,
  // because the numbers are only worth reading together.
  const parts = [
    data.here_count === 0
      ? t('admin.now.nobodyHere')
      : tn('admin.now.someoneHere', 'admin.now.somePeopleHere', data.here_count),
    data.quiet_for_s !== null ? t('admin.now.quietFor', { d: fmtDuration(data.quiet_for_s) }) : null,
    t('admin.now.costSoFar', { cost: fmtUsd(data.session.cost_usd) }),
  ].filter(Boolean)
  return (
    <Callout tone="warn" title={tn('admin.now.idleOne', 'admin.now.idleMany', data.pods_up)}>
      {parts.join(' · ')}
    </Callout>
  )
}

function PersonRow({ person, isSelf }: { person: ActivityPerson; isSelf: boolean }) {
  const t = useT()
  const p = person
  return (
    <TableRow className={cn(!p.here && 'text-muted-foreground')}>
      <TableCell className="ps-5 font-medium">
        <span className="inline-flex items-center gap-2">
          <Avatar email={p.email} url={p.avatar_url} size="xs" />
          {p.email}
          {isSelf ? <Badge variant="secondary">{t('common.you')}</Badge> : null}
        </span>
      </TableCell>
      <TableCell>
        <span className="inline-flex items-center gap-1.5">
          <span className={cn('size-1.5 shrink-0 rounded-full',
                              p.here ? 'bg-ok shadow-[0_0_0_3px_rgb(79_208_138/0.18)]' : 'bg-faint')} />
          {p.here
            ? t('admin.now.here')
            : p.seen_s_ago === null
              ? t('common.never')
              : t('admin.now.ago', { d: fmtDuration(p.seen_s_ago) })}
        </span>
      </TableCell>
      <TableCell
        className="tabular-nums"
        title={p.using_for_s === null ? undefined : t(p.here ? 'admin.now.thisStretch' : 'admin.now.lastStretch')}
      >
        {p.using_for_s === null ? '—' : fmtDuration(p.using_for_s)}
      </TableCell>
      <TableCell className="tabular-nums">
        {p.acted_s_ago === null ? '—' : t('admin.now.ago', { d: fmtDuration(p.acted_s_ago) })}
      </TableCell>
      <TableCell className="text-end tabular-nums">{p.queued || '—'}</TableCell>
      <TableCell className="pe-5 text-end tabular-nums">{p.running || '—'}</TableCell>
    </TableRow>
  )
}

/** Who is on the app and for how long, beside what the GPUs are doing about it.
 *
 *  First tab on purpose: "is this thing running for nothing" is the question
 *  asked most often, and answering it used to mean reading the pod panel, the
 *  queue and the users table and guessing how they lined up in time.
 */
export function ActivityTab() {
  const t = useT()
  const me = useMe()
  const activity = useAdminActivity()
  const data = activity.data

  if (activity.isPending) {
    return (
      <div className="grid gap-2" aria-label={t('admin.now.loading')}>
        <Skeleton className="h-28 w-full rounded-xl" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    )
  }
  if (!data) {
    return (
      <Panel title={t('admin.now.title')} description={t('admin.now.desc')}>
        <EmptyState
          icon={WifiOff}
          title={t('admin.now.loadFailed')}
          action={
            <Button size="sm" variant="outline" onClick={() => void activity.refetch()}>
              {t('common.tryAgain')}
            </Button>
          }
        />
      </Panel>
    )
  }

  return (
    <div className="grid gap-4">
      <Panel title={t('admin.now.title')} description={t('admin.now.desc')}>
        <div className="grid gap-4">
          <Verdict data={data} />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label={t('admin.now.statHere')} value={String(data.here_count)}
                  tone={data.here_count > 0 ? 'ok' : undefined} />
            <Stat label={t('admin.now.statWaiting')} value={String(data.queued)} />
            <Stat label={t('admin.now.statRendering')} value={String(data.running)} />
            <Stat label={t('admin.now.statPods')} value={String(data.pods_up)}
                  tone={data.verdict === 'idle' ? 'warn' : undefined} />
            <Stat label={t('admin.now.statCost')} value={fmtUsd(data.session.cost_usd)} />
          </div>
        </div>
      </Panel>

      <Panel
        title={t('admin.now.people')}
        description={t('admin.now.peopleDesc', { d: fmtDuration(data.here_window_s) })}
        className="p-0 [&>header]:p-5 [&>header]:pb-0"
      >
        {data.people.length === 0 ? (
          <EmptyState icon={Users} title={t('admin.now.nobody')} />
        ) : (
          <Table className="text-sm">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="ps-5">{t('admin.now.col.person')}</TableHead>
                <TableHead>{t('admin.now.col.status')}</TableHead>
                <TableHead>{t('admin.now.col.for')}</TableHead>
                <TableHead>{t('admin.now.col.did')}</TableHead>
                <TableHead className="text-end">{t('admin.now.col.waiting')}</TableHead>
                <TableHead className="pe-5 text-end">{t('admin.now.col.rendering')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.people.map((p) => (
                <PersonRow key={p.id} person={p} isSelf={p.id === me.data?.id} />
              ))}
            </TableBody>
          </Table>
        )}
      </Panel>
    </div>
  )
}
