import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { useCreateProject, useRenameProject } from '@/api/mutations'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useT } from '@/i18n'

/** Names a new project, or renames `rename` when it is given. */
export function NewProjectDialog({
  open,
  onOpenChange,
  rename,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  rename?: { id: string; name: string }
}) {
  const t = useT()
  const navigate = useNavigate()
  const create = useCreateProject()
  const renameProject = useRenameProject(rename?.id ?? '')
  const action = rename ? renameProject : create
  const [name, setName] = useState(rename?.name ?? '')
  const trimmed = name.trim()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!trimmed || action.isPending) return
    // A failure is toasted by the mutation, and the dialog stays open to try again.
    if (rename) {
      renameProject.mutate(trimmed, { onSuccess: () => onOpenChange(false) })
      return
    }
    create.mutate(trimmed, {
      onSuccess: (p) => {
        onOpenChange(false)
        void navigate(`/p/${p.id}`)
      },
    })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setName(rename?.name ?? '')
        onOpenChange(o)
      }}
    >
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{t(rename ? 'project.rename' : 'projects.new')}</DialogTitle>
          </DialogHeader>
          <label className="grid gap-1.5 text-sm font-medium">
            {t('projects.name.label')}
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('projects.name.placeholder')}
              maxLength={80}
              autoFocus
            />
          </label>
          <DialogFooter>
            <Button type="submit" disabled={!trimmed || action.isPending}>
              {action.isPending ? <Loader2 className="animate-spin" /> : null}
              {t(rename ? 'project.rename' : 'projects.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
