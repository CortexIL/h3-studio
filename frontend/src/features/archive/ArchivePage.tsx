import { Download, Film, Loader2, Search, SearchX, WifiOff, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { useArchive, useStatus } from '@/api/queries'
import { EmptyState } from '@/components/app/EmptyState'
import { Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useViewerList } from '@/features/viewer/useClipViewer'
import { MODE_LABEL, presetLabel } from '@/lib/format'
import { useDebouncedValue, useDocumentTitle } from '@/lib/hooks'
import { FILTER_MODES } from '@/lib/modes'
import { cn } from '@/lib/utils'

import { ClipCard } from './ClipCard'
import { useClipSelection } from './useClipSelection'

// Radix selects can't hold an empty value, so "no filter" needs a name.
const ALL = 'all'
const GRID = 'grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4'

function isField(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  )
}

export function ArchivePage() {
  useDocumentTitle('Archive')
  const [params, setParams] = useSearchParams()
  const preset = params.get('preset') ?? ''
  const mode = params.get('mode') ?? ''
  const [text, setText] = useState(() => params.get('q') ?? '')
  const debounced = useDebouncedValue(text.trim(), 300)
  // Typing waits for a pause; clearing the box is instant.
  const q = text.trim() === '' ? '' : debounced
  const presets = useStatus().data?.config.presets
  const searchBox = useRef<HTMLInputElement>(null)

  const archive = useArchive({ q, preset, mode })
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = archive
  const clips = useMemo(() => archive.data?.pages.flatMap((p) => p.clips) ?? [], [archive.data])

  const clipIds = useMemo(() => clips.map((c) => c.id), [clips])
  const { gridRef, selected, pick, clear: clearSelection, selectAll, band } = useClipSelection(clipIds)

  const downloadSelected = () => {
    // Only clips that still have a file: asking for one that is gone would take
    // the whole zip down with it.
    const ids = clips.filter((c) => selected.has(c.id) && c.video_url).map((c) => c.id)
    if (!ids.length) return
    // A link rather than fetch-then-blob: the server streams the zip, so the
    // browser never holds a hundred and forty megabytes in a JavaScript string.
    const a = document.createElement('a')
    a.href = `/api/archive/download?ids=${ids.join(',')}`
    document.body.append(a)
    a.click()
    a.remove()
    toast.success(`Downloading ${ids.length} clip${ids.length === 1 ? '' : 's'} as a zip`)
  }

  const setParam = useCallback(
    (key: string, value: string) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value) next.set(key, value)
          else next.delete(key)
          return next
        },
        { replace: true },
      ),
    [setParams],
  )

  // The search is in the URL, so a filtered archive can be bookmarked.
  useEffect(() => {
    if ((params.get('q') ?? '') !== q) setParam('q', q)
  }, [q, params, setParam])

  useEffect(() => {
    useViewerList.getState().setIds(clips.map((c) => c.id))
  }, [clips])

  // "/" jumps to the search box, as on most sites with one.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey || isField(e.target)) return
      e.preventDefault()
      searchBox.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Load the next page before the reader reaches the end of this one.
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage()
      },
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const filtered = Boolean(q || preset || mode)
  const clearFilters = () => {
    setText('')
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const key of ['q', 'preset', 'mode']) next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  const count = clips.length
  const plural = count !== 1 || hasNextPage
  const summary = `${count}${hasNextPage ? '+' : ''} ${plural ? 'clips' : 'clip'}${filtered ? (plural ? ' match' : ' matches') : ''}`
  const presetKeys = [...new Set([...Object.keys(presets ?? {}), ...(preset ? [preset] : [])])]

  return (
    <Page title="Archive" description="Every clip you've made. Only you can see these." width="wide">
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={searchBox}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && text) {
                  e.preventDefault()
                  setText('')
                }
              }}
              enterKeyHint="search"
              placeholder="Search your prompts"
              aria-label="Search your prompts"
              className="h-9 pr-9 pl-9"
            />
            {text ? (
              <button
                type="button"
                onClick={() => {
                  setText('')
                  searchBox.current?.focus()
                }}
                aria-label="Clear the search"
                className="absolute top-1/2 right-2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" />
              </button>
            ) : (
              <kbd className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 rounded border px-1.5 font-sans text-2xs text-muted-foreground">
                /
              </kbd>
            )}
          </div>
          <Select value={preset || ALL} onValueChange={(v) => setParam('preset', v === ALL ? '' : v)}>
            <SelectTrigger aria-label="Quality" className="h-9 w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All qualities</SelectItem>
              {presetKeys.map((key) => (
                <SelectItem key={key} value={key}>
                  {presetLabel(key)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={mode || ALL} onValueChange={(v) => setParam('mode', v === ALL ? '' : v)}>
            <SelectTrigger aria-label="Mode" className="h-9 w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All modes</SelectItem>
              {FILTER_MODES.map((m) => (
                <SelectItem key={m} value={m}>
                  {MODE_LABEL[m]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {archive.data ? (
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {summary}
            {filtered ? (
              <>
                {' · '}
                <button type="button" onClick={clearFilters} className="text-foreground underline-offset-2 hover:underline">
                  Clear filters
                </button>
              </>
            ) : null}
          </p>
        ) : null}
      </div>

      {archive.isPending ? (
        <div className={GRID} aria-label="Loading your clips">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="overflow-hidden rounded-lg border">
              <Skeleton className="aspect-video rounded-none" />
              <div className="grid gap-2 p-3">
                <Skeleton className="h-3.5 w-11/12" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          ))}
        </div>
      ) : archive.isError && count === 0 ? (
        <EmptyState
          icon={WifiOff}
          title="Couldn't load your archive"
          description="The server didn't answer."
          action={
            <Button size="sm" variant="outline" onClick={() => void archive.refetch()}>
              Try again
            </Button>
          }
        />
      ) : count === 0 ? (
        filtered ? (
          <EmptyState
            icon={SearchX}
            title="No clips match"
            description={q ? `Nothing in your archive mentions “${q}” with these filters.` : 'Try another quality or mode.'}
            action={
              <Button size="sm" variant="outline" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={Film}
            title="No clips yet"
            description="Clips you make in the Studio land here, and only you can see them."
            action={
              <Button size="sm" asChild>
                <Link to="/">Go to the Studio</Link>
              </Button>
            }
          />
        )
      ) : (
        <>
          {selected.size > 0 ? (
            <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-lg border bg-card/95 px-3 py-2 backdrop-blur">
              <span className="text-sm font-medium tabular-nums">{selected.size} selected</span>
              <div className="flex-1" />
              {selected.size < count ? (
                <Button size="sm" variant="ghost" onClick={selectAll}>
                  Select all {count}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={clearSelection}>
                Clear
              </Button>
              <Button size="sm" onClick={downloadSelected}>
                <Download /> Download {selected.size}
              </Button>
            </div>
          ) : null}
          <div
            ref={gridRef}
            className={cn(
              GRID,
              archive.isPlaceholderData && 'opacity-60 transition-opacity',
              band && 'select-none',
            )}
          >
            {clips.map((clip) => (
              <ClipCard key={clip.id} clip={clip} selected={selected.has(clip.id)} onPick={pick} />
            ))}
          </div>
          <div ref={sentinel} aria-hidden />
          {hasNextPage ? (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => void fetchNextPage()} disabled={isFetchingNextPage}>
                {isFetchingNextPage ? <Loader2 className="animate-spin" /> : null}
                Load more
              </Button>
            </div>
          ) : null}
        </>
      )}

      {band ? (
        <div
          aria-hidden
          className="pointer-events-none fixed z-30 rounded-sm border border-primary/70 bg-primary/15"
          style={{
            left: band.left,
            top: band.top,
            width: band.right - band.left,
            height: band.bottom - band.top,
          }}
        />
      ) : null}
    </Page>
  )
}
