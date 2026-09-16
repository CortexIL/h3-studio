import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { edgeScroller, scrollParent } from '@/lib/edgeScroll'

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
  // Ids picked without being on screen: everything "select all" fetched from the
  // server. They are not in `ids`, and without this they would be pruned out of
  // the selection the moment they were put into it.
  const [offscreen, setOffscreen] = useState<ReadonlySet<string>>(new Set())

  // Clips that have gone - deleted, or filtered out of the page - stop counting.
  // Derived rather than pruned in an effect, so a vanished clip is never briefly
  // still part of the total.
  const selected = useMemo(() => {
    if (picked.size === 0) return picked
    const here = new Set(ids)
    return new Set([...picked].filter((id) => here.has(id) || offscreen.has(id)))
  }, [picked, ids, offscreen])

  // What is picked, where the band can read it without depending on it. The
  // band's effect used to list `selected` among its dependencies, and every
  // sweep changes the selection: the first move past the threshold re-ran the
  // effect, which tore down the very mousemove and mouseup listeners driving
  // the drag. The band froze where it was, nothing was picked, and the
  // rectangle stayed on screen because the mouseup that clears it was gone too.
  const selectedRef = useRef<ReadonlySet<string>>(selected)
  useEffect(() => {
    selectedRef.current = selected
  }, [selected])

  const clear = useCallback(() => {
    setOffscreen(new Set())
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

  /** Pick these exact ids, on screen or not - which is what "select all" means. */
  const selectIds = useCallback((all: string[]) => {
    setOffscreen(new Set(all))
    setPicked(new Set(all))
  }, [])

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
    // The anchor is held in document coordinates, because the page scrolls under
    // it: a band pressed against the bottom edge keeps growing from where it
    // began, not from wherever that point has since drifted to on screen.
    let start: { x: number; y: number } | null = null
    let pointer = { x: 0, y: 0 }
    let base: ReadonlySet<string> = new Set()
    let live = false
    let startedOnCard = false

    const boxes = () =>
      [...grid.querySelectorAll<HTMLElement>('[data-clip-id]')].map((el) => {
        const r = el.getBoundingClientRect()
        return { id: el.dataset.clipId as string, box: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } }
      })

    /** Redraw the band and re-pick, from wherever the pointer and the page are now. */
    const paint = () => {
      if (!start || !live) return
      const doc = bandFrom(start, pointer.x + window.scrollX, pointer.y + window.scrollY)
      // Back into viewport coordinates: that is where the cards report
      // themselves to be, and where the rectangle is drawn.
      const view = {
        left: doc.left - window.scrollX,
        right: doc.right - window.scrollX,
        top: doc.top - window.scrollY,
        bottom: doc.bottom - window.scrollY,
      }
      setBand(view)
      setPicked(new Set([...base, ...idsInBand(boxes(), view)]))
    }

    // Held against the top or bottom edge, the page keeps scrolling - and every
    // step re-picks, so the cards arriving under the band are caught by it.
    const scroller = edgeScroller(scrollParent(grid), paint)

    const onMove = (e: MouseEvent) => {
      if (!start) return
      pointer = { x: e.clientX, y: e.clientY }
      const travelled = Math.hypot(
        e.clientX + window.scrollX - start.x,
        e.clientY + window.scrollY - start.y,
      )
      if (!live && travelled < BAND_THRESHOLD) return
      // Past the threshold this is a sweep, not a click: stop the browser
      // turning it into a text selection across every caption it crosses, and
      // drop whatever the first few pixels already selected.
      if (!live) window.getSelection?.()?.removeAllRanges()
      live = true
      e.preventDefault()
      scroller.track(e.clientY)
      paint()
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('scroll', paint, true)
      scroller.stop()
      // A click on the empty space drops the selection, as it does in a file
      // manager: it is also how someone finds out that the space is live.
      if (!live && !startedOnCard) clear()
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
      const el = e.target as HTMLElement
      // Nothing begins under a dialog, including the one asking whether to
      // delete the very clips a sweep would be changing underneath it.
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return
      if (el.closest('[data-no-band]')) return
      const card = el.closest('[data-clip-id]')
      // A sweep may begin on a card - the poster is most of the grid, and the
      // threshold above tells a sweep from a click - or on the empty space
      // around the cards, which is where a file manager expects one to start.
      // Anywhere else on the page a press means something already: the search
      // box, the toolbar, the navigation.
      if (!card && el.closest('a, button, input, select, textarea, [role="menu"], [role="listbox"]')) return
      startedOnCard = card !== null
      pointer = { x: e.clientX, y: e.clientY }
      start = { x: e.clientX + window.scrollX, y: e.clientY + window.scrollY }
      // Holding a modifier adds to what is already picked; a bare sweep replaces it.
      base = e.shiftKey || e.metaKey || e.ctrlKey ? selectedRef.current : new Set()
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
      // A wheel turned mid-sweep moves the cards under a rectangle that would
      // otherwise go on describing where they used to be.
      window.addEventListener('scroll', paint, true)
    }

    // On the document rather than on the grid: the natural place to start a
    // marquee is the empty margin beside the cards, and the grid is only as wide
    // as the cards themselves. A press that lands on any other control has
    // already been let go above.
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('scroll', paint, true)
      scroller.stop()
    }
    // `grid` alone: a listener re-registered mid-sweep is a sweep that stops halfway.
  }, [grid, clear])

  return { gridRef: setGrid, selected, pick, clear, selectIds, band }
}
