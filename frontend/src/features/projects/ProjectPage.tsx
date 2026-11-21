import { FolderX } from 'lucide-react'
import { Link, NavLink, Outlet } from 'react-router'

import { useProject } from '@/api/queries'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'

import { useProjectId } from './useProjectId'

const tab = ({ isActive }: { isActive: boolean }) =>
  cn(
    'border-b-2 px-3 py-2 text-sm font-medium transition-colors',
    isActive ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
  )

export function ProjectPage() {
  const t = useT()
  const project = useProject(useProjectId())

  // flex-1 / min-h-0 all the way down: the Studio below fills the window only if every parent lets it.
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {project.data ? (
        <>
          <header className="flex flex-wrap items-end gap-x-6 border-b px-3 pt-3 lg:px-4">
            <h1 className="truncate pb-2 text-lg font-semibold">{project.data.name}</h1>
            <nav aria-label={project.data.name} className="flex">
              <NavLink to="." end className={tab}>
                {t('project.tab.studio')}
              </NavLink>
              <NavLink to="clips" className={tab}>
                {t('project.tab.clips')}
              </NavLink>
            </nav>
          </header>
          <Outlet />
        </>
      ) : project.isPending ? (
        <div className="p-4">
          <Skeleton className="h-7 w-48" />
        </div>
      ) : (
        <EmptyState
          icon={FolderX}
          title={t('project.missing.title')}
          description={t('project.missing.body')}
          action={
            <Button size="sm" variant="outline" asChild>
              <Link to="/">{t('project.backHome')}</Link>
            </Button>
          }
        />
      )}
    </div>
  )
}
