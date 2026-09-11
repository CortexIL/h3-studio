import { Loader2, ZoomIn, ZoomOut } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'

import { useUploadAvatar } from '@/api/mutations'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { errorMessage } from '@/lib/errors'

// The square you see, and the square that is sent. The server shrinks it to
// 256px and re-encodes it, so this only has to be generous, not exact.
const VIEW = 280
const OUT = 512
const MAX_ZOOM = 4
const CANT_OPEN = "This picture can't be opened here. Try a JPEG, PNG or WebP."

interface Size {
  w: number
  h: number
}
interface Point {
  x: number
  y: number
}

/** The picture's size on screen: just covering the square at zoom 1. */
function fit(size: Size, zoom: number): Size {
  const scale = Math.max(VIEW / size.w, VIEW / size.h) * zoom
  return { w: size.w * scale, h: size.h * scale }
}

/** Keep the picture covering the whole square, so no empty edge can be dragged in. */
function clamp(p: Point, shown: Size): Point {
  return {
    x: Math.min(0, Math.max(VIEW - shown.w, p.x)),
    y: Math.min(0, Math.max(VIEW - shown.h, p.y)),
  }
}

export function AvatarCropDialog({ file, onClose }: { file: File; onClose: () => void }) {
  const upload = useUploadAvatar()
  const img = useRef<HTMLImageElement>(null)
  const drag = useRef<{ start: Point; from: Point } | null>(null)
  const [size, setSize] = useState<Size | null>(null)
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 })
  const [error, setError] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  // A data URL, not an object URL: an object URL revoked in an effect cleanup is
  // dead when React runs the effect again (StrictMode does, on purpose), and the
  // picture then silently fails to load.
  useEffect(() => {
    const reader = new FileReader()
    reader.onload = () => setUrl(typeof reader.result === 'string' ? reader.result : null)
    reader.onerror = () => setError(CANT_OPEN)
    reader.readAsDataURL(file)
    return () => reader.abort()
  }, [file])
  const shown = size ? fit(size, zoom) : null

  const onLoad = () => {
    const el = img.current
    if (!el?.naturalWidth || !el.naturalHeight) {
      setError(CANT_OPEN)
      return
    }
    const natural = { w: el.naturalWidth, h: el.naturalHeight }
    const start = fit(natural, 1)
    setSize(natural)
    setOffset({ x: (VIEW - start.w) / 2, y: (VIEW - start.h) / 2 })
  }

  // Zoom around the middle of the square, not its corner.
  const zoomTo = (next: number) => {
    if (!size) return
    const z = Math.min(MAX_ZOOM, Math.max(1, next))
    const after = fit(size, z)
    const k = after.w / fit(size, zoom).w
    const c = VIEW / 2
    setOffset((o) => clamp({ x: c - (c - o.x) * k, y: c - (c - o.y) * k }, after))
    setZoom(z)
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (!shown) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { start: { x: e.clientX, y: e.clientY }, from: offset }
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || !shown) return
    setOffset(clamp({ x: d.from.x + e.clientX - d.start.x, y: d.from.y + e.clientY - d.start.y }, shown))
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!shown) return
    const step = e.shiftKey ? 40 : 10
    const move: Record<string, Point> = {
      ArrowLeft: { x: -step, y: 0 },
      ArrowRight: { x: step, y: 0 },
      ArrowUp: { x: 0, y: -step },
      ArrowDown: { x: 0, y: step },
    }
    const m = move[e.key]
    if (m) {
      e.preventDefault()
      setOffset((o) => clamp({ x: o.x + m.x, y: o.y + m.y }, shown))
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault()
      zoomTo(zoom + 0.25)
    } else if (e.key === '-') {
      e.preventDefault()
      zoomTo(zoom - 0.25)
    }
  }

  const save = () => {
    const el = img.current
    if (!el || !shown) return
    const canvas = document.createElement('canvas')
    canvas.width = OUT
    canvas.height = OUT
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      setError("Your browser couldn't prepare the picture.")
      return
    }
    const r = OUT / VIEW
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(el, offset.x * r, offset.y * r, shown.w * r, shown.h * r)
    canvas.toBlob((blob) => {
      if (!blob) {
        setError("Your browser couldn't prepare the picture.")
        return
      }
      setError(null)
      upload.mutate(new File([blob], 'avatar.png', { type: 'image/png' }), {
        onSuccess: onClose,
        onError: (err) => setError(errorMessage(err)),
      })
    }, 'image/png')
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !upload.isPending && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Crop your picture</DialogTitle>
          <DialogDescription>Drag to move it and zoom to fit. The circle is what shows.</DialogDescription>
        </DialogHeader>
        <div className="grid justify-items-center gap-4">
          <div
            role="group"
            aria-label="Picture to crop. Drag or use the arrow keys to move it; plus and minus zoom."
            tabIndex={0}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
            onKeyDown={onKeyDown}
            className="relative cursor-grab touch-none overflow-hidden rounded-xl bg-black outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/50 active:cursor-grabbing"
            style={{ width: VIEW, height: VIEW }}
          >
            {url ? (
              <img
                ref={img}
                src={url}
                alt="Picture to crop"
                draggable={false}
                onLoad={onLoad}
                onError={() => setError(CANT_OPEN)}
                className="pointer-events-none absolute max-w-none"
                style={shown ? { left: offset.x, top: offset.y, width: shown.w, height: shown.h } : { opacity: 0 }}
              />
            ) : error ? null : (
              <Loader2 aria-label="Opening the picture" className="absolute inset-0 m-auto size-6 animate-spin text-white/60" />
            )}
            {/* Dim everything outside the circle. */}
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 rounded-full shadow-[0_0_0_9999px_rgb(0_0_0/0.55)] ring-1 ring-white/40"
            />
          </div>
          <div className="flex w-full items-center gap-3">
            <ZoomOut className="size-4 shrink-0 text-muted-foreground" />
            <input
              type="range"
              min={1}
              max={MAX_ZOOM}
              step={0.01}
              value={zoom}
              onChange={(e) => zoomTo(Number(e.target.value))}
              disabled={!shown}
              aria-label="Zoom"
              className="h-1.5 flex-1 cursor-pointer accent-primary disabled:cursor-default"
            />
            <ZoomIn className="size-4 shrink-0 text-muted-foreground" />
          </div>
          {error ? (
            <p role="alert" className="text-center text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={upload.isPending}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!shown || upload.isPending}>
            {upload.isPending ? <Loader2 className="animate-spin" /> : null}
            Save picture
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
