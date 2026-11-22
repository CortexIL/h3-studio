import { FolderOpen, Plus, WifiOff } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { useProjects } from '@/api/queries'
import type { ProjectSummary } from '@/api/types'
import logo from '@/assets/logo.png'
import { EmptyState } from '@/components/app/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { tn, useT } from '@/i18n'
import { useDocumentTitle } from '@/lib/hooks'

import { NewProjectDialog } from './NewProjectDialog'

function ProjectCard({ project }: { project: ProjectSummary }) {
  const t = useT()
  return (
    <Link to={`/p/${project.id}`} className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
      <Card className="h-full gap-0 overflow-hidden py-0 transition-colors hover:bg-accent/40">
        {/* Absolute so a portrait poster is cropped to 16:9 instead of stretching the card. */}
        <div className="relative grid aspect-video place-items-center overflow-hidden bg-muted">
          {project.cover_url ? (
            <img src={project.cover_url} alt="" className="absolute inset-0 size-full object-cover" />
          ) : (
            <img src={logo} alt="" className="size-12 rounded-md opacity-70" />
          )}
        </div>
        <div className="grid gap-1.5 p-3">
          <p className="truncate font-medium">{project.name}</p>
          <p className="text-sm text-muted-foreground">
            {tn('projects.clips.one', 'projects.clips.many', project.clips)} ·{' '}
            {tn('projects.people.one', 'projects.people.many', project.members)}
          </p>
          {project.my_role !== 'owner' ? (
            <Badge variant="secondary" className="w-fit">
              {t('projects.shared')}
            </Badge>
          ) : null}
        </div>
      </Card>
    </Link>
  )
}

export function ProjectsPage() {
  const t = useT()
  useDocumentTitle(t('projects.title'))
  const { data, isPending, isError, refetch } = useProjects()
  const [creating, setCreating] = useState(false)
  const newButton = (
    <Button onClick={() => setCreating(true)}>
      <Plus />
      {t('projects.new')}
    </Button>
  )

  return (
    <div className="mx-auto grid w-full max-w-6xl content-start gap-4 p-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{t('projects.title')}</h1>
        {data?.length ? newButton : null}
      </div>
      {/* A list already on screen stays there when a background refresh fails. */}
      {isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((p) => (
            <ProjectCard key={p.id} project={p} />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          icon={WifiOff}
          title={t('projects.error.title')}
          description={t('projects.error.body')}
          action={<Button onClick={() => void refetch()}>{t('common.tryAgain')}</Button>}
        />
      ) : (
        <EmptyState icon={FolderOpen} title={t('projects.empty.title')} description={t('projects.empty.body')} action={newButton} />
      )}
      <NewProjectDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}
