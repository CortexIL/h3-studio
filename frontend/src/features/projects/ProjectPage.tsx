import { FolderX, MoreHorizontal, Share2 } from 'lucide-react'
import { useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { useDeleteProject } from '@/api/mutations'
import { useProject } from '@/api/queries'
import type { Project } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'

import { NewProjectDialog } from './NewProjectDialog'
import { ShareDialog } from './ShareDialog'
import { useProjectId } from './useProjectId'

const tab = ({ isActive }: { isActive: boolean }) =>
  cn(
    'border-b-2 px-3 py-2 text-sm font-medium transition-colors',
    isActive ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
  )

/** Share for everyone in the project; rename and delete for the owner. */
function ProjectActions({ project }: { project: Project }) {
  const t = useT()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const del = useDeleteProject(project.id)
  const [sharing, setSharing] = useState(false)
  const [renaming, setRenaming] = useState(false)

  const onDelete = () =>
    void confirm({
      title: t('project.delete'),
      description: t('project.delete.confirm', { name: project.name }),
      confirmLabel: t('project.delete'),
      destructive: true,
      action: async () => {
        try {
          await del.mutateAsync()
        } catch (err) {
          // The hook already toasted the server's words; 409 gets the plainer copy, and the dialog closes.
          if (!(err instanceof ApiError && err.status === 409)) throw err
          toast.error(t('project.delete.busy'))
          return
        }
        void navigate('/')
      },
    })

  return (
    <div className="ms-auto flex items-center gap-1 pb-1">
      <Button size="sm" variant="outline" onClick={() => setSharing(true)}>
        <Share2 />
        {t('share.open')}
      </Button>
      {project.my_role === 'owner' ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="ghost" className="size-8" aria-label={t('project.actions')}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => setRenaming(true)}>{t('project.rename')}</DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              {t('project.delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {sharing ? <ShareDialog project={project} open onOpenChange={setSharing} /> : null}
      {renaming ? <NewProjectDialog open onOpenChange={setRenaming} rename={project} /> : null}
    </div>
  )
}

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
            <ProjectActions project={project.data} />
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
