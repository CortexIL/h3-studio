import { Page } from '@/components/app/Page'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useDocumentTitle } from '@/lib/hooks'
import { useLayout } from '@/lib/layout'
import { cn } from '@/lib/utils'
import { useT, type Key } from '@/i18n'

import { useBetaFeatures, type BetaFeature, type BetaStatus } from './features'

const STATUS: Record<BetaStatus, { label: Key; className: string }> = {
  available: { label: 'beta.status.available', className: 'border-success/40 text-success' },
  experimental: { label: 'beta.status.experimental', className: 'border-warn/40 text-warn' },
  planned: { label: 'beta.status.planned', className: 'text-muted-foreground' },
}

function FeatureCard({ feature }: { feature: BetaFeature }) {
  const t = useT()
  const status = STATUS[feature.status]
  return (
    <Card id={feature.id} className="scroll-mt-20">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{feature.title}</CardTitle>
          <Badge variant="outline" className={status.className}>
            {t(status.label)}
          </Badge>
        </div>
        <CardDescription>{feature.summary}</CardDescription>
        {feature.status !== 'planned' ? (
          <p className={cn('text-2xs', feature.verified ? 'text-success' : 'text-warn')}>
            {t(feature.verified ? 'beta.checked' : 'beta.notChecked')}
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <section>
          <h3 className="mb-1 font-medium">{t('beta.whatModel')}</h3>
          <p className="text-muted-foreground">{feature.why}</p>
        </section>
        {feature.howTo.length ? (
          <section>
            <h3 className="mb-1 font-medium">{t('beta.howTo')}{feature.where ? ` · ${feature.where}` : ''}</h3>
            <ol className="list-decimal space-y-1 ps-5 text-muted-foreground">
              {feature.howTo.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </section>
        ) : null}
        {feature.limits.length ? (
          <section>
            <h3 className="mb-1 font-medium">{t('beta.limits')}</h3>
            <ul className="list-disc space-y-1 ps-5 text-muted-foreground">
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
  const t = useT()
  const features = useBetaFeatures()
  useDocumentTitle(t('beta.docTitle'))
  const layout = useLayout((s) => s.layout)
  const toggleLayout = useLayout((s) => s.toggle)
  const counts = features.reduce<Record<BetaStatus, number>>(
    (acc, f) => ({ ...acc, [f.status]: acc[f.status] + 1 }),
    { available: 0, experimental: 0, planned: 0 },
  )
  return (
    <Page
      title={t('beta.title')}
      description={t('beta.desc', { a: counts.available, e: counts.experimental, p: counts.planned })}
    >
      {features.some((f) => f.status !== 'planned' && !f.verified) ? (
        <p className="rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-sm">{t('beta.banner')}</p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
        <p className="text-muted-foreground">{t(layout === 'classic' ? 'classic.backLine' : 'classic.betaLine')}</p>
        <Button size="sm" variant="outline" onClick={toggleLayout}>
          {t(layout === 'classic' ? 'classic.backButton' : 'classic.betaButton')}
        </Button>
      </div>
      <nav aria-label={t('beta.features')} className="flex flex-wrap gap-1.5">
        {features.map((f) => (
          <a
            key={f.id}
            href={`#${f.id}`}
            className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
          >
            {f.title}
          </a>
        ))}
      </nav>
      {features.map((f) => (
        <FeatureCard key={f.id} feature={f} />
      ))}
    </Page>
  )
}
