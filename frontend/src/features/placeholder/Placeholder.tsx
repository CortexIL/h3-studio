import { Page } from '@/components/app/Page'
import { useDocumentTitle } from '@/lib/hooks'

// Stands in for the pages still being built, so the router is complete.
export function Placeholder({ title }: { title: string }) {
  useDocumentTitle(title)
  return (
    <Page title={title}>
      <p className="text-muted-foreground">Coming in the next step of the rebuild.</p>
    </Page>
  )
}

export function NotFound() {
  useDocumentTitle('Not found')
  return (
    <Page title="Page not found" description="That address doesn't match anything in H3 Studio.">
      <a href="/" className="text-primary underline-offset-4 hover:underline">
        Back to the studio
      </a>
    </Page>
  )
}
