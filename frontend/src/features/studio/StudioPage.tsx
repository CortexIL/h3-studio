import { Eye } from 'lucide-react'

import { useProject } from '@/api/queries'
import { EmptyState } from '@/components/app/EmptyState'
import { ResizableSplit } from '@/components/app/ResizableSplit'
import { useProjectId } from '@/features/projects/useProjectId'
import { useT } from '@/i18n'
import { useDocumentTitle } from '@/lib/hooks'
import { useLayout } from '@/lib/layout'

import { ClassicComposePanel } from './ClassicComposePanel'
import { ComposePanel } from './ComposePanel'
import { FeedPanel } from './FeedPanel'

function ViewerNotice() {
  const t = useT()
  return <EmptyState icon={Eye} title={t('project.viewer.title')} description={t('project.viewer.body')} />
}

export function StudioPage() {
  const project = useProject(useProjectId()).data
  useDocumentTitle(project ? project.name : 'H3 Studio')
  const viewer = project?.my_role === 'viewer'
  const layout = useLayout((s) => s.layout)
  return (
    // On wide screens the two panels fill the window under the header (and the
    // notice strip, when there is one) and scroll on their own; on narrow ones
    // they stack and the page scrolls. See AppShell.
    <div data-fill-viewport className="flex min-h-0 flex-1 flex-col p-3 lg:p-4">
      {/* Keyed on the layout so every name in the feed re-reads the dictionary when it flips. */}
      <ResizableSplit key={layout} left={viewer ? <ViewerNotice /> : layout === 'classic' ? <ClassicComposePanel /> : <ComposePanel />} right={<FeedPanel />} />
    </div>
  )
}
