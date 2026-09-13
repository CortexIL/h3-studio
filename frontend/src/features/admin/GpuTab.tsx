import { AlertTriangle, KeyRound, Loader2, WifiOff } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { useSavePromptKey, useSaveRunpodKey, useSetBudget, useSetPolicy } from '@/api/mutations'
import { useAdminStatus, useKeyState, usePromptKeyState } from '@/api/queries'
import type { AdminStatus, Policy, PodState } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { EmptyState } from '@/components/app/EmptyState'
import { Field } from '@/components/app/Field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { errorMessage } from '@/lib/errors'
import { useT, type Key } from '@/i18n'
import { fmtDuration, fmtUsd, podStateLabel } from '@/lib/format'
import { cn } from '@/lib/utils'

import { Callout, Panel } from './Panel'

// The server refuses anything above this (MAX_BUDGET_USD in app/routes/admin.py).
const MAX_BUDGET = 1000

const DOT: Record<PodState, string> = {
  off: 'bg-faint',
  booting: 'bg-info animate-pulse',
  ready: 'bg-ok',
  stopping: 'bg-warn animate-pulse',
  error: 'bg-destructive',
}

const POLICIES: { value: Policy; label: Key; help: Key }[] = [
  { value: 'auto', label: 'admin.policy.auto', help: 'admin.policy.autoHelp' },
  { value: 'keep-warm', label: 'admin.policy.keepWarm', help: 'admin.policy.keepWarmHelp' },
  { value: 'off', label: 'admin.policy.off', help: 'admin.policy.offHelp' },
]

const policyKey = (p: Policy): Key => POLICIES.find((x) => x.value === p)?.label ?? 'admin.policy.auto'

function Stat({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs text-muted-foreground">{label}</dt>
      <dd className={cn('truncate text-sm tabular-nums', mono && 'font-mono text-xs leading-5')}>{value}</dd>
    </div>
  )
}

function PodPanel({ s }: { s: AdminStatus }) {
  const t = useT()
  const { pod, session } = s
  const pct = session.limit_usd > 0 ? Math.min(100, (session.cost_usd / session.limit_usd) * 100) : 0
  const tone =
    session.cost_usd >= session.limit_usd ? 'bg-destructive' : session.cost_usd >= session.warn_usd ? 'bg-warn' : 'bg-primary'
  const up = pod.state === 'ready' || pod.state === 'booting' || pod.state === 'stopping'
  return (
    <Panel
      title={t('admin.gpu')}
      className="lg:col-span-2"
      actions={
        <Badge variant="outline" className={s.backend === 'mock' ? 'border-warn/40 text-warn' : undefined}>
          {t(s.backend === 'mock' ? 'admin.demoBackend' : 'admin.runpod')}
        </Badge>
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex h-7 items-center gap-2 rounded-full border px-3 text-sm font-medium">
          <span className={cn('size-2 rounded-full', DOT[pod.state])} />
          {podStateLabel(pod.state)}
        </span>
        {pod.gpu ? (
          <span className="text-sm">
            {pod.gpu} <span className="text-muted-foreground">{t('admin.perHour', { rate: fmtUsd(pod.rate_per_hour) })}</span>
          </span>
        ) : (
          <span className="text-sm text-muted-foreground">{t('admin.noGpu')}</span>
        )}
      </div>
      {/* The detail matters while a pod starts or fails; once it's up it only repeats the GPU line. */}
      {pod.detail && !(pod.gpu && pod.detail.startsWith(pod.gpu)) ? (
        <p className="mt-2 text-sm text-muted-foreground">{pod.detail}</p>
      ) : null}

      <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label={t('admin.upFor')} value={up ? fmtDuration(pod.uptime_s) : '—'} />
        <Stat label={t('admin.queued')} value={s.counts.queued} />
        <Stat label={t('admin.generating')} value={s.counts.running} />
        <Stat label={t('admin.pod')} value={pod.pod_id ?? '—'} mono />
      </dl>

      <div className="mt-5 grid gap-2">
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span>{t('admin.thisSession')}</span>
          <span className="tabular-nums">
            <b className="font-semibold">{fmtUsd(session.cost_usd)}</b>{' '}
            <span className="text-muted-foreground">{t('admin.of', { limit: fmtUsd(session.limit_usd) })}</span>
          </span>
        </div>
        <div
          role="progressbar"
          aria-label={t('admin.progressAria')}
          aria-valuemin={0}
          aria-valuemax={session.limit_usd}
          aria-valuenow={session.cost_usd}
          className="h-2 overflow-hidden rounded-full bg-secondary"
        >
          <div className={cn('h-full rounded-full transition-[width] duration-500', tone)} style={{ width: `${pct}%` }} />
        </div>
        <p className="text-xs text-muted-foreground">
          {t('admin.warnAt', { warn: fmtUsd(session.warn_usd), limit: fmtUsd(session.limit_usd) })}
        </p>
      </div>

      {s.error ? (
        <Callout tone="destructive" title={t('admin.lastError')} className="mt-5">
          {s.error}
        </Callout>
      ) : null}
    </Panel>
  )
}

function PolicyPanel({ s }: { s: AdminStatus }) {
  const t = useT()
  const setPolicy = useSetPolicy()
  const confirm = useConfirm()
  // Show the new choice while it's being saved; the next poll confirms it.
  const value = setPolicy.isPending && setPolicy.variables ? setPolicy.variables : s.policy

  const choose = (next: Policy) => {
    if (next === s.policy) return
    const run = () => setPolicy.mutateAsync(next).then(() => void toast.success(t('admin.policySet', { label: t(policyKey(next)) })))
    if (next === 'auto') {
      run().catch(() => {}) // the mutation already reports the error
      return
    }
    void confirm(
      next === 'off'
        ? {
            title: t('admin.offTitle'),
            description: t('admin.offDesc'),
            confirmLabel: t('admin.turnOff'),
            destructive: true,
            action: run,
          }
        : {
            title: t('admin.warmTitle'),
            description:
              s.pod.rate_per_hour > 0
                ? t('admin.warmDescRate', { rate: fmtUsd(s.pod.rate_per_hour) })
                : t('admin.warmDesc'),
            confirmLabel: t('admin.keepWarm'),
            action: run,
          },
    )
  }

  return (
    <Panel title={t('admin.policyTitle')} description={t('admin.policyDesc')}>
      <ToggleGroup
        type="single"
        variant="outline"
        value={value}
        onValueChange={(v) => v && choose(v as Policy)}
        aria-label={t('admin.policyAria')}
        className="w-full"
      >
        {POLICIES.map((p) => (
          <ToggleGroupItem key={p.value} value={p.value} className="h-9 flex-1 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
            {t(p.label)}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <p className="mt-3 text-sm text-muted-foreground" aria-live="polite">
        {t(POLICIES.find((p) => p.value === value)?.help ?? 'admin.policy.autoHelp')}
      </p>
    </Panel>
  )
}

function BudgetPanel({ limit }: { limit: number }) {
  const t = useT()
  const setBudget = useSetBudget()
  // What the admin is typing, or null - then the field follows the server.
  const [draft, setDraft] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)
  const text = draft ?? String(limit)
  const value = Number(text)
  const invalid =
    text.trim() === '' || !Number.isFinite(value)
      ? t('admin.enterAmount')
      : value <= 0
        ? t('admin.moreThanZero')
        : value > MAX_BUDGET
          ? t('admin.mostIs', { max: fmtUsd(MAX_BUDGET, 0) })
          : null
  const changed = draft !== null && value !== limit

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (invalid || !changed) return
    setServerError(null)
    setBudget.mutate(value, {
      onSuccess: () => setDraft(null),
      onError: (err) => setServerError(errorMessage(err)),
    })
  }

  return (
    <Panel
      title={t('admin.budgetTitle')}
      description={t('admin.budgetDesc')}
    >
      <form onSubmit={submit}>
        <Field label={t('admin.limitUsd')} error={(draft !== null ? invalid : null) ?? serverError}>
          {(props) => (
            <div className="flex gap-2">
              <div className="relative flex-1">
                <span className="pointer-events-none absolute top-1/2 start-3 -translate-y-1/2 text-muted-foreground">$</span>
                <Input
                  {...props}
                  inputMode="decimal"
                  value={text}
                  onChange={(e) => {
                    setDraft(e.target.value)
                    setServerError(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setDraft(null)
                  }}
                  className="h-9 ps-7 tabular-nums"
                />
              </div>
              <Button type="submit" className="h-9" disabled={!changed || Boolean(invalid) || setBudget.isPending}>
                {setBudget.isPending ? <Loader2 className="animate-spin" /> : null}
                {t('common.save')}
              </Button>
            </div>
          )}
        </Field>
      </form>
    </Panel>
  )
}

function KeyPanel() {
  const t = useT()
  const state = useKeyState()
  const save = useSaveRunpodKey()
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const value = key.trim()
    if (!value) {
      setError(t('admin.pasteFirst'))
      return
    }
    setError(null)
    save.mutate(value, {
      onSuccess: (r) => {
        setKey('')
        setNote(r.note)
        toast.success(t('admin.keySaved'))
      },
      onError: (err) => setError(errorMessage(err)),
    })
  }

  return (
    <Panel
      title={t('admin.keyTitle')}
      className="lg:col-span-2"
      description={t('admin.keyDesc')}
      actions={note ? <Badge variant="outline" className="shrink-0 border-warn/40 text-warn">{t('admin.restartRequired')}</Badge> : null}
    >
      <div className="mb-4 flex items-center gap-2 text-sm">
        {state.isPending ? (
          <Skeleton className="h-5 w-56" />
        ) : state.data?.present ? (
          <>
            <KeyRound className="size-4 text-ok" />
            <span>
              {t('admin.keyLoadedBefore')}<code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs">…{state.data.hint}</code>{t('admin.keyLoadedAfter')}
            </span>
          </>
        ) : (
          <>
            <AlertTriangle className="size-4 text-warn" />
            <span>{t('admin.noKey')}</span>
          </>
        )}
      </div>
      <form onSubmit={submit}>
        <Field label={t('admin.newKey')} error={error} hint={note}>
          {(props) => (
            <div className="flex flex-wrap gap-2 sm:flex-nowrap">
              <Input
                {...props}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={key}
                onChange={(e) => {
                  setKey(e.target.value)
                  setError(null)
                }}
                placeholder={t('admin.pasteKey')}
                className="h-9 min-w-0 flex-1 font-mono"
              />
              <Button type="submit" className="h-9" disabled={save.isPending}>
                {save.isPending ? (
                  <>
                    <Loader2 className="animate-spin" /> {t('admin.checking')}
                  </>
                ) : (
                  t('admin.verifySave')
                )}
              </Button>
            </div>
          )}
        </Field>
      </form>
    </Panel>
  )
}

function PromptKeyPanel() {
  const t = useT()
  const state = usePromptKeyState()
  const save = useSavePromptKey()
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const value = key.trim()
    if (!value) {
      setError(t('admin.pasteFirst'))
      return
    }
    setError(null)
    save.mutate(value, {
      onSuccess: () => {
        setKey('')
        toast.success(t('admin.promptKeySaved'))
      },
      onError: (err) => setError(errorMessage(err)),
    })
  }

  return (
    <Panel title={t('admin.promptKeyTitle')} className="lg:col-span-2" description={t('admin.promptKeyDesc')}>
      <div className="mb-4 flex items-center gap-2 text-sm">
        {state.isPending ? (
          <Skeleton className="h-5 w-56" />
        ) : state.data?.present ? (
          <>
            <KeyRound className="size-4 text-ok" />
            <span>
              {t('admin.promptKeyLoadedBefore')}<code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs">…{state.data.hint}</code>{t('admin.promptKeyLoadedAfter')}
            </span>
          </>
        ) : (
          <>
            <AlertTriangle className="size-4 text-warn" />
            <span>{t('admin.noPromptKey')}</span>
          </>
        )}
      </div>
      <form onSubmit={submit}>
        <Field label={t('admin.newKey')} error={error}>
          {(props) => (
            <div className="flex flex-wrap gap-2 sm:flex-nowrap">
              <Input
                {...props}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={key}
                onChange={(e) => {
                  setKey(e.target.value)
                  setError(null)
                }}
                placeholder={t('admin.pastePromptKey')}
                className="h-9 min-w-0 flex-1 font-mono"
              />
              <Button type="submit" className="h-9" disabled={save.isPending}>
                {save.isPending ? (
                  <>
                    <Loader2 className="animate-spin" /> {t('admin.checkingPromptKey')}
                  </>
                ) : (
                  t('admin.verifySave')
                )}
              </Button>
            </div>
          )}
        </Field>
      </form>
    </Panel>
  )
}

export function GpuTab() {
  const t = useT()
  const status = useAdminStatus()
  if (status.isPending) {
    return (
      <div className="grid gap-4 lg:grid-cols-2" aria-label={t('admin.loadingGpu')}>
        <Skeleton className="h-64 rounded-xl lg:col-span-2" />
        <Skeleton className="h-40 rounded-xl" />
        <Skeleton className="h-40 rounded-xl" />
      </div>
    )
  }
  if (!status.data) {
    return (
      <EmptyState
        icon={WifiOff}
        title={t('admin.loadGpuFailed')}
        action={
          <Button size="sm" variant="outline" onClick={() => void status.refetch()}>
            {t('common.tryAgain')}
          </Button>
        }
      />
    )
  }
  const s = status.data
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {!s.leader ? (
        <Callout tone="warn" title={t('admin.notLeader')} className="lg:col-span-2">
          {t('admin.notLeaderDesc')}
        </Callout>
      ) : null}
      {s.notice ? <Callout tone="info" title={s.notice} className="lg:col-span-2" /> : null}
      <PodPanel s={s} />
      <PolicyPanel s={s} />
      <BudgetPanel limit={s.session.limit_usd} />
      <KeyPanel />
      <PromptKeyPanel />
    </div>
  )
}
