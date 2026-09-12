import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { Box } from './selection'
import { BAND_THRESHOLD, bandFrom, idsInBand, rangeBetween } from './selection'

/**
 * Selecting clips: by checkbox, by modifier-click, by range, or by dragging a
 * band across the grid.
 *
 * The band reads the cards' positions from the DOM rather than from React,
 * because the grid decides where they are and only the browser knows the answer.
 */
export function useClipSelection(ids: string[]) {
  // The grid arrives as state, not as a ref: a ref object's identity never
  // changes, so an effect watching one runs before the grid exists and never
  // again - which is a listener that is never attached and a band that never
  // appears.
  const [grid, setGrid] = useState<HTMLElement | null>(null)
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set())
  const [band, setBand] = useState<Box | null>(null)
  const anchor = useRef<string | null>(null)
  const idsRef = useRef(ids)
  useEffect(() => {
    idsRef.current = ids
  }, [ids])

  // Clips that have gone - deleted, or filtered out of the page - stop counting.
  // Derived rather than pruned in an effect, so a vanished clip is never briefly
  // still part of the total.
  const selected = useMemo(() => {
    if (picked.size === 0) return picked
    const here = new Set(ids)
    return new Set([...picked].filter((id) => here.has(id)))
  }, [picked, ids])

  const clear = useCallback(() => {
    setPicked(new Set())
    anchor.current = null
  }, [])

  const pick = useCallback((id: string, event: { shiftKey: boolean }) => {
    setPicked((prev) => {
      const next = new Set(prev)
      if (event.shiftKey && anchor.current) {
        for (const inRange of rangeBetween(idsRef.current, anchor.current, id)) next.add(inRange)
        return next
      }
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    if (!event.shiftKey) anchor.current = id
  }, [])

  const selectAll = useCallback(() => setPicked(new Set(idsRef.current)), [])

  // Escape clears, the way it does in every file manager.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clear()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [clear])

  useEffect(() => {
    if (!grid) return
    let start: { x: number; y: number } | null = null
    let base: ReadonlySet<string> = new Set()
    let live = false

    const boxes = () =>
      [...grid.querySelectorAll<HTMLElement>('[data-clip-id]')].map((el) => {
        const r = el.getBoundingClientRect()
        return { id: el.dataset.clipId as string, box: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } }
      })

    const onMove = (e: MouseEvent) => {
      if (!start) return
      if (!live && Math.hypot(e.clientX - start.x, e.clientY - start.y) < BAND_THRESHOLD) return
      // Past the threshold this is a sweep, not a click: stop the browser
      // turning it into a text selection across every caption it crosses.
      live = true
      e.preventDefault()
      const box = bandFrom(start, e.clientX, e.clientY)
      setBand(box)
      const touched = idsInBand(boxes(), box)
      setPicked(new Set([...base, ...touched]))
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      if (live) {
        // A sweep that happens to end on the card it started on would otherwise
        // also register as a click, and open the viewer over the selection.
        const swallow = (e: MouseEvent) => {
          e.stopPropagation()
          e.preventDefault()
        }
        window.addEventListener('click', swallow, { capture: true, once: true })
        setTimeout(() => window.removeEventListener('click', swallow, true), 0)
      }
      start = null
      live = false
      setBand(null)
    }

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return
      // A sweep may begin on a card - the poster is most of the grid, and the
      // threshold above already tells a sweep from a click. Only the controls
      // where a drag would mean something else are left alone.
      if ((e.target as HTMLElement).closest('a, input, select, textarea, [data-no-band]')) return
      start = { x: e.clientX, y: e.clientY }
      // Holding a modifier adds to what is already picked; a bare sweep replaces it.
      base = e.shiftKey || e.metaKey || e.ctrlKey ? selected : new Set()
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    }

    grid.addEventListener('mousedown', onDown)
    return () => {
      grid.removeEventListener('mousedown', onDown)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [grid, selected])

  return { gridRef: setGrid, selected, pick, clear, selectAll, band }
}
