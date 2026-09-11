import { Link } from 'react-router'

import { Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { useDocumentTitle } from '@/lib/hooks'

export function NotFound() {
  useDocumentTitle('Not found')
  return (
    <Page title="Page not found" description="That address doesn't match anything in H3 Studio.">
      <div>
        <Button asChild variant="outline">
          <Link to="/">Back to the Studio</Link>
        </Button>
      </div>
    </Page>
  )
}
