import { Film, Loader2 } from 'lucide-react'
import { useState } from 'react'

import { request } from '@/api/client'
import { useArchive } from '@/api/queries'
import type { Clip } from '@/api/types'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/errors'
import { useDebouncedValue } from '@/lib/hooks'

import { useCompose } from './composeStore'

/** What the server hands back once it has cut the tail off a finished clip. */
interface SourceCut {
  key: string
  name: string
  preset: string
  seconds: number
}

export function ExtendSourceDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [query, setQuery] = useState('')
  const debounced = useDebouncedValue(query, 300)
  // Any finished clip can be continued, so no mode filter here.
  const archive = useArchive({ q: debounced })
  const [cutting, setCutting] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  const clips = archive.data?.pages.flatMap((p) => p.clips) ?? []

  const choose = async (clip: Clip) => {
    setCutting(clip.id)
    setFailed(null)
    try {
      const cut = await request<SourceCut>(`/api/jobs/${clip.id}/extend-source`, { method: 'POST' })
      const { setExtendSource, setPreset, setMode } = useCompose.getState()
      setExtendSource({
        from: 'clip',
        label: clip.prompt,
        posterUrl: clip.poster_url ?? undefined,
        tile: { id: `clip-${clip.id}`, name: cut.name, status: 'ready', progress: 1, key: cut.key },
      })
      // The guide is cropped to fit the render size, so continuing a draft clip
      // at a different quality would continue a cropped version of it.
      setPreset(cut.preset)
      setMode('extend')
      onOpenChange(false)
    } catch (err) {
      setFailed(errorMessage(err))
    } finally {
      setCutting(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Choose a video to continue</DialogTitle>
          <DialogDescription>
            The new clip picks up from the last moment of the one you choose.
          </DialogDescription>
        </DialogHeader>

        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search your clips"
          aria-label="Search your clips"
        />

        {failed ? <p className="text-xs text-destructive">{failed}</p> : null}

        <div className="max-h-[50vh] overflow-y-auto">
          {archive.isPending ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="aspect-video w-full rounded-md" />
              ))}
            </div>
          ) : clips.length === 0 ? (
            <EmptyState
              icon={Film}
              title={debounced ? 'Nothing matches' : 'No finished clips yet'}
              description={
                debounced
                  ? 'Try a different search.'
                  : 'Make a clip first, then you can continue it.'
              }
            />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
              {clips.map((clip) => (
                <button
                  key={clip.id}
                  type="button"
                  // A clip whose file is gone cannot be continued.
                  disabled={!clip.video_url || cutting !== null}
                  onClick={() => void choose(clip)}
                  className="group overflow-hidden rounded-md border bg-field text-left outline-none transition-colors hover:border-primary/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
                  aria-label={`Continue ${clip.prompt.slice(0, 60)}`}
                >
                  <div className="relative grid aspect-video place-items-center bg-black">
                    {clip.poster_url ? (
                      <img src={clip.poster_url} alt="" className="size-full object-cover" />
                    ) : (
                      <Film className="size-5 text-muted-foreground" />
                    )}
                    {cutting === clip.id ? (
                      <div className="absolute inset-0 grid place-items-center bg-background/70">
                        <Loader2 className="size-5 animate-spin" />
                      </div>
                    ) : null}
                  </div>
                  <p className="line-clamp-2 px-2 py-1.5 text-xs text-muted-foreground">
                    {clip.prompt}
                  </p>
                </button>
              ))}
            </div>
          )}

          {archive.hasNextPage ? (
            <div className="mt-3 grid place-items-center">
              <Button
                size="sm"
                variant="outline"
                disabled={archive.isFetchingNextPage}
                onClick={() => void archive.fetchNextPage()}
              >
                {archive.isFetchingNextPage ? 'Loading…' : 'Load more'}
              </Button>
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  )
}
