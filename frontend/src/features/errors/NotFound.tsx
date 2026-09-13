import { Link } from 'react-router'

import { Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { useDocumentTitle } from '@/lib/hooks'
import { useT } from '@/i18n'

export function NotFound() {
  const t = useT()
  useDocumentTitle(t('notFound.docTitle'))
  return (
    <Page title={t('notFound.title')} description={t('notFound.desc')}>
      <div>
        <Button asChild variant="outline">
          <Link to="/">{t('notFound.back')}</Link>
        </Button>
      </div>
    </Page>
  )
}
