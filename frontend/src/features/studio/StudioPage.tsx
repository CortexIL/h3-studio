import { ResizableSplit } from '@/components/app/ResizableSplit'
import { useDocumentTitle } from '@/lib/hooks'

import { ComposePanel } from './ComposePanel'
import { FeedPanel } from './FeedPanel'

export function StudioPage() {
  useDocumentTitle('H3 Studio')
  return (
    // On wide screens the two panels fill the window under the header (and the
    // notice strip, when there is one) and scroll on their own; on narrow ones
    // they stack and the page scrolls. See AppShell.
    <div data-fill-viewport className="flex min-h-0 flex-1 flex-col p-3 lg:p-4">
      <ResizableSplit left={<ComposePanel />} right={<FeedPanel />} />
    </div>
  )
}
