import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { useCreateProject } from '@/api/mutations'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useT } from '@/i18n'

export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT()
  const navigate = useNavigate()
  const create = useCreateProject()
  const [name, setName] = useState('')
  const trimmed = name.trim()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!trimmed || create.isPending) return
    // A failure is toasted by the mutation, and the dialog stays open to try again.
    create.mutate(trimmed, {
      onSuccess: (p) => {
        onOpenChange(false)
        setName('')
        void navigate(`/p/${p.id}`)
      },
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{t('projects.new')}</DialogTitle>
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
            <Button type="submit" disabled={!trimmed || create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" /> : null}
              {t('projects.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
