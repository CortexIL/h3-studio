import { Page } from '@/components/app/Page'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useDocumentTitle } from '@/lib/hooks'
import { cn } from '@/lib/utils'

import { BETA_FEATURES, type BetaFeature, type BetaStatus } from './features'

const STATUS: Record<BetaStatus, { label: string; className: string }> = {
  available: { label: 'Available', className: 'border-success/40 text-success' },
  experimental: { label: 'Experimental', className: 'border-warn/40 text-warn' },
  planned: { label: 'Planned', className: 'text-muted-foreground' },
}

function FeatureCard({ feature }: { feature: BetaFeature }) {
  const status = STATUS[feature.status]
  return (
    <Card id={feature.id} className="scroll-mt-20">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{feature.title}</CardTitle>
          <Badge variant="outline" className={status.className}>
            {status.label}
          </Badge>
        </div>
        <CardDescription>{feature.summary}</CardDescription>
        {feature.status !== 'planned' ? (
          <p className={cn('text-2xs', feature.verified ? 'text-success' : 'text-warn')}>
            {feature.verified ? 'Checked on the GPU' : 'Built and tested offline · not yet checked on the GPU'}
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <section>
          <h3 className="mb-1 font-medium">What the model does</h3>
          <p className="text-muted-foreground">{feature.why}</p>
        </section>
        {feature.howTo.length ? (
          <section>
            <h3 className="mb-1 font-medium">How to use it{feature.where ? ` · ${feature.where}` : ''}</h3>
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
              {feature.howTo.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </section>
        ) : null}
        {feature.limits.length ? (
          <section>
            <h3 className="mb-1 font-medium">Limits</h3>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              {feature.limits.map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function BetaPage() {
  useDocumentTitle('Beta')
  const counts = BETA_FEATURES.reduce<Record<BetaStatus, number>>(
    (acc, f) => ({ ...acc, [f.status]: acc[f.status] + 1 }),
    { available: 0, experimental: 0, planned: 0 },
  )
  return (
    <Page
      title="Beta"
      description={`Everything the model can do that the studio is learning to expose. ${counts.available} available · ${counts.experimental} experimental · ${counts.planned} planned.`}
    >
      {BETA_FEATURES.some((f) => f.status !== 'planned' && !f.verified) ? (
        <p className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-sm">
          Beta: everything below is built and covered by tests, and each feature is checked on a real GPU one by one. Until a
          card says so, expect the first render to teach us something.
        </p>
      ) : null}
      <nav aria-label="Features" className="flex flex-wrap gap-1.5">
        {BETA_FEATURES.map((f) => (
          <a
            key={f.id}
            href={`#${f.id}`}
            className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
          >
            {f.title}
          </a>
        ))}
      </nav>
      {BETA_FEATURES.map((f) => (
        <FeatureCard key={f.id} feature={f} />
      ))}
    </Page>
  )
}
