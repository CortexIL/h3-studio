import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { ApiError } from '@/api/client'
import { useAddMember, useLeaveProject, useRemoveMember, useSetMemberRole } from '@/api/mutations'
import type { Project, ProjectRole } from '@/api/types'
import { Avatar } from '@/components/app/Avatar'
import { useConfirm } from '@/components/app/confirm'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useT } from '@/i18n'

const ROLES = ['editor', 'viewer'] as const

function RoleSelect({ value, onChange, label }: { value: ProjectRole; onChange: (r: ProjectRole) => void; label?: string }) {
  const t = useT()
  return (
    <Select value={value} onValueChange={(v) => onChange(v as ProjectRole)}>
      <SelectTrigger className="h-8 w-44" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((r) => (
          <SelectItem key={r} value={r}>
            {t(`share.role.${r}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function ShareDialog({ project, open, onOpenChange }: { project: Project; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const add = useAddMember(project.id)
  const setRole = useSetMemberRole(project.id)
  const remove = useRemoveMember(project.id)
  const leave = useLeaveProject(project.id)
  const [email, setEmail] = useState('')
  const [role, setNewRole] = useState<ProjectRole>('editor')
  const [problem, setProblem] = useState<string | null>(null)
  const isOwner = project.my_role === 'owner'
  const me = project.members.find((m) => m.is_me)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const address = email.trim()
    if (!address || add.isPending) return
    setProblem(null)
    add.mutate(
      { email: address, role },
      {
        onSuccess: () => setEmail(''),
        onError: (err) => {
          if (err instanceof ApiError && err.status === 404) setProblem(t('share.notFound'))
          else if (err instanceof ApiError && err.status === 409) setProblem(t('share.already'))
        },
      },
    )
  }

  const onLeave = () =>
    void confirm({
      title: t('share.leave'),
      description: t('share.leave.confirm', { name: project.name }),
      confirmLabel: t('share.leave'),
      destructive: true,
      action: async () => {
        if (!me) return
        await leave.mutateAsync(me.user_id)
        void navigate('/')
      },
    })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('share.title', { name: project.name })}</DialogTitle>
          <DialogDescription>{t('share.hint')}</DialogDescription>
        </DialogHeader>
        <ul className="grid gap-2">
          {project.members.map((m) => (
            <li key={m.user_id} className="flex items-center gap-2">
              <Avatar email={m.email} url={m.avatar_url} />
              <span className="min-w-0 flex-1 truncate text-sm">{m.email}</span>
              {isOwner && m.role !== 'owner' ? (
                <>
                  <RoleSelect
                    value={m.role}
                    label={m.email}
                    onChange={(r) => setRole.mutate({ userId: m.user_id, role: r })}
                  />
                  <Button size="sm" variant="ghost" onClick={() => remove.mutate(m.user_id)}>
                    {t('share.remove')}
                  </Button>
                </>
              ) : (
                <span className="text-sm text-muted-foreground">{t(`share.role.${m.role}`)}</span>
              )}
            </li>
          ))}
        </ul>
        {isOwner ? (
          <form onSubmit={submit} className="grid gap-2">
            <div className="flex gap-2">
              <Input
                type="email"
                aria-label={t('share.email')}
                placeholder={t('share.email')}
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value)
                  setProblem(null)
                }}
              />
              <RoleSelect value={role} onChange={setNewRole} />
              <Button type="submit" disabled={!email.trim() || add.isPending}>
                {add.isPending ? <Loader2 className="animate-spin" /> : null}
                {t('share.add')}
              </Button>
            </div>
            {problem ? (
              <p role="alert" className="text-sm text-destructive">
                {problem}
              </p>
            ) : null}
          </form>
        ) : (
          <Button variant="outline" onClick={onLeave}>
            {t('share.leave')}
          </Button>
        )}
      </DialogContent>
    </Dialog>
  )
}
