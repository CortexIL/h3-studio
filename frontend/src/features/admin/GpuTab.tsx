import { AlertTriangle, KeyRound, Loader2, WifiOff } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { useSaveRunpodKey, useSetBudget, useSetPolicy } from '@/api/mutations'
import { useAdminStatus, useKeyState } from '@/api/queries'
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
import { POD_STATE_LABEL, fmtDuration, fmtUsd } from '@/lib/format'
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

const POLICIES: { value: Policy; label: string; help: string }[] = [
  { value: 'auto', label: 'Auto', help: 'Rents a GPU when there is work and shuts it down after the idle window.' },
  { value: 'keep-warm', label: 'Keep warm', help: 'Holds the GPU between takes, so the next clip starts at once. It bills the whole time.' },
  { value: 'off', label: 'Off', help: 'No GPU. Clips wait in the queue until you switch back.' },
]

const policyLabel = (p: Policy) => POLICIES.find((x) => x.value === p)?.label ?? p

function Stat({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs text-muted-foreground">{label}</dt>
      <dd className={cn('truncate text-sm tabular-nums', mono && 'font-mono text-xs leading-5')}>{value}</dd>
    </div>
  )
}

function PodPanel({ s }: { s: AdminStatus }) {
  const { pod, session } = s
  const pct = session.limit_usd > 0 ? Math.min(100, (session.cost_usd / session.limit_usd) * 100) : 0
  const tone =
    session.cost_usd >= session.limit_usd ? 'bg-destructive' : session.cost_usd >= session.warn_usd ? 'bg-warn' : 'bg-primary'
  const up = pod.state === 'ready' || pod.state === 'booting' || pod.state === 'stopping'
  return (
    <Panel
      title="GPU"
      className="lg:col-span-2"
      actions={
        <Badge variant="outline" className={s.backend === 'mock' ? 'border-warn/40 text-warn' : undefined}>
          {s.backend === 'mock' ? 'Demo backend · no GPU' : 'RunPod'}
        </Badge>
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex h-7 items-center gap-2 rounded-full border px-3 text-sm font-medium">
          <span className={cn('size-2 rounded-full', DOT[pod.state])} />
          {POD_STATE_LABEL[pod.state]}
        </span>
        {pod.gpu ? (
          <span className="text-sm">
            {pod.gpu} <span className="text-muted-foreground">· {fmtUsd(pod.rate_per_hour)}/hr</span>
          </span>
        ) : (
          <span className="text-sm text-muted-foreground">No GPU rented right now</span>
        )}
      </div>
      {/* The detail matters while a pod starts or fails; once it's up it only repeats the GPU line. */}
      {pod.detail && !(pod.gpu && pod.detail.startsWith(pod.gpu)) ? (
        <p className="mt-2 text-sm text-muted-foreground">{pod.detail}</p>
      ) : null}

      <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Up for" value={up ? fmtDuration(pod.uptime_s) : '—'} />
        <Stat label="Queued" value={s.counts.queued} />
        <Stat label="Generating" value={s.counts.running} />
        <Stat label="Pod" value={pod.pod_id ?? '—'} mono />
      </dl>

      <div className="mt-5 grid gap-2">
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span>This session</span>
          <span className="tabular-nums">
            <b className="font-semibold">{fmtUsd(session.cost_usd)}</b>{' '}
            <span className="text-muted-foreground">of {fmtUsd(session.limit_usd)}</span>
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Session cost against the budget"
          aria-valuemin={0}
          aria-valuemax={session.limit_usd}
          aria-valuenow={session.cost_usd}
          className="h-2 overflow-hidden rounded-full bg-secondary"
        >
          <div className={cn('h-full rounded-full transition-[width] duration-500', tone)} style={{ width: `${pct}%` }} />
        </div>
        <p className="text-xs text-muted-foreground">
          A warning shows at {fmtUsd(session.warn_usd)}. At {fmtUsd(session.limit_usd)} the pod is shut down and the
          policy is set to Off.
        </p>
      </div>

      {s.error ? (
        <Callout tone="destructive" title="The last pod error" className="mt-5">
          {s.error}
        </Callout>
      ) : null}
    </Panel>
  )
}

function PolicyPanel({ s }: { s: AdminStatus }) {
  const setPolicy = useSetPolicy()
  const confirm = useConfirm()
  // Show the new choice while it's being saved; the next poll confirms it.
  const value = setPolicy.isPending && setPolicy.variables ? setPolicy.variables : s.policy

  const choose = (next: Policy) => {
    if (next === s.policy) return
    const run = () => setPolicy.mutateAsync(next).then(() => void toast.success(`GPU policy set to ${policyLabel(next)}`))
    if (next === 'auto') {
      run().catch(() => {}) // the mutation already reports the error
      return
    }
    void confirm(
      next === 'off'
        ? {
            title: 'Turn the GPU off?',
            description:
              'The pod is shut down now. A clip that is rendering goes back to the queue, and nothing starts until you switch back to Auto.',
            confirmLabel: 'Turn off',
            destructive: true,
            action: run,
          }
        : {
            title: 'Keep the GPU warm?',
            description:
              s.pod.rate_per_hour > 0
                ? `It stays rented between takes and bills about ${fmtUsd(s.pod.rate_per_hour)} an hour until you switch back.`
                : 'It stays rented between takes and bills the whole time until you switch back.',
            confirmLabel: 'Keep warm',
            action: run,
          },
    )
  }

  return (
    <Panel title="Policy" description="When the shared GPU runs.">
      <ToggleGroup
        type="single"
        variant="outline"
        value={value}
        onValueChange={(v) => v && choose(v as Policy)}
        aria-label="GPU policy"
        className="w-full"
      >
        {POLICIES.map((p) => (
          <ToggleGroupItem key={p.value} value={p.value} className="h-9 flex-1 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
            {p.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <p className="mt-3 text-sm text-muted-foreground" aria-live="polite">
        {POLICIES.find((p) => p.value === value)?.help}
      </p>
    </Panel>
  )
}

function BudgetPanel({ limit }: { limit: number }) {
  const setBudget = useSetBudget()
  // What the admin is typing, or null - then the field follows the server.
  const [draft, setDraft] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)
  const text = draft ?? String(limit)
  const value = Number(text)
  const invalid =
    text.trim() === '' || !Number.isFinite(value)
      ? 'Enter an amount in dollars.'
      : value <= 0
        ? 'It has to be more than $0.'
        : value > MAX_BUDGET
          ? `The most it can be is ${fmtUsd(MAX_BUDGET, 0)}.`
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
      title="Session budget"
      description="When one GPU session costs this much, the pod is shut down and the policy set to Off. It's the backstop between a bug and a bill."
    >
      <form onSubmit={submit}>
        <Field label="Limit (USD)" error={(draft !== null ? invalid : null) ?? serverError}>
          {(props) => (
            <div className="flex gap-2">
              <div className="relative flex-1">
                <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground">$</span>
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
                  className="h-9 pl-7 tabular-nums"
                />
              </div>
              <Button type="submit" className="h-9" disabled={!changed || Boolean(invalid) || setBudget.isPending}>
                {setBudget.isPending ? <Loader2 className="animate-spin" /> : null}
                Save
              </Button>
            </div>
          )}
        </Field>
      </form>
    </Panel>
  )
}

function KeyPanel() {
  const state = useKeyState()
  const save = useSaveRunpodKey()
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const value = key.trim()
    if (!value) {
      setError('Paste a key first.')
      return
    }
    setError(null)
    save.mutate(value, {
      onSuccess: (r) => {
        setKey('')
        setNote(r.note)
        toast.success('RunPod key verified and saved')
      },
      onError: (err) => setError(errorMessage(err)),
    })
  }

  return (
    <Panel
      title="RunPod key"
      className="lg:col-span-2"
      description="Checked with RunPod before it's stored, kept in the database so it survives a redeploy, and never sent back to a browser."
      actions={note ? <Badge variant="outline" className="shrink-0 border-warn/40 text-warn">Restart required</Badge> : null}
    >
      <div className="mb-4 flex items-center gap-2 text-sm">
        {state.isPending ? (
          <Skeleton className="h-5 w-56" />
        ) : state.data?.present ? (
          <>
            <KeyRound className="size-4 text-ok" />
            <span>
              A key ending in <code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs">…{state.data.hint}</code> is loaded.
            </span>
          </>
        ) : (
          <>
            <AlertTriangle className="size-4 text-warn" />
            <span>No key is loaded, so the GPU can't start.</span>
          </>
        )}
      </div>
      <form onSubmit={submit}>
        <Field label="New key" error={error} hint={note}>
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
                placeholder="Paste a RunPod API key"
                className="h-9 min-w-0 flex-1 font-mono"
              />
              <Button type="submit" className="h-9" disabled={save.isPending}>
                {save.isPending ? (
                  <>
                    <Loader2 className="animate-spin" /> Checking with RunPod…
                  </>
                ) : (
                  'Verify & save'
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
  const status = useAdminStatus()
  if (status.isPending) {
    return (
      <div className="grid gap-4 lg:grid-cols-2" aria-label="Loading the GPU status">
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
        title="Couldn't load the GPU status"
        action={
          <Button size="sm" variant="outline" onClick={() => void status.refetch()}>
            Try again
          </Button>
        }
      />
    )
  }
  const s = status.data
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {!s.leader ? (
        <Callout tone="warn" title="This copy of the app isn't running the queue" className="lg:col-span-2">
          It won't start or stop a GPU. More than one replica is running; scale the service back to one.
        </Callout>
      ) : null}
      {s.notice ? <Callout tone="info" title={s.notice} className="lg:col-span-2" /> : null}
      <PodPanel s={s} />
      <PolicyPanel s={s} />
      <BudgetPanel limit={s.session.limit_usd} />
      <KeyPanel />
    </div>
  )
}
