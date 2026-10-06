import { expect, test } from 'vitest'

import { edgeScroller, scrollParent } from './edgeScroll'

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms))

function scrollable(top: number, bottom: number) {
  const el = document.createElement('div')
  el.style.overflowY = 'auto'
  Object.defineProperty(el, 'scrollHeight', { value: 4000 })
  Object.defineProperty(el, 'clientHeight', { value: bottom - top })
  el.getBoundingClientRect = () =>
    ({ top, bottom, left: 0, right: 500, width: 500, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect
  document.body.append(el)
  return el
}

test('a pointer held near the bottom edge keeps scrolling', async () => {
  const el = scrollable(0, 400)
  const scroller = edgeScroller(el)
  scroller.track(395)
  await wait(60)
  const moved = el.scrollTop
  expect(moved).toBeGreaterThan(0)

  // Still held: it keeps going, without another move.
  await wait(60)
  expect(el.scrollTop).toBeGreaterThan(moved)
  scroller.stop()

  const stopped = el.scrollTop
  await wait(60)
  expect(el.scrollTop).toBe(stopped)
})

test('a pointer in the middle scrolls nothing', async () => {
  const el = scrollable(0, 400)
  const scroller = edgeScroller(el)
  scroller.track(200)
  await wait(60)
  expect(el.scrollTop).toBe(0)
  scroller.stop()
})

test('the top edge scrolls back up', async () => {
  const el = scrollable(0, 400)
  el.scrollTop = 500
  const scroller = edgeScroller(el)
  scroller.track(5)
  await wait(60)
  expect(el.scrollTop).toBeLessThan(500)
  scroller.stop()
})

test('every step tells the drag to look again', async () => {
  const el = scrollable(0, 400)
  let steps = 0
  const scroller = edgeScroller(el, () => {
    steps += 1
  })
  scroller.track(399)
  await wait(60)
  scroller.stop()
  // Whatever the drag is drawing is computed from positions the scroll just moved.
  expect(steps).toBeGreaterThan(1)
})

test('the scrolling ancestor is the one that actually scrolls', () => {
  const outer = scrollable(0, 400)
  const plain = document.createElement('div')
  const card = document.createElement('div')
  plain.append(card)
  outer.append(plain)
  expect(scrollParent(card)).toBe(outer)

  const loose = document.createElement('div')
  document.body.append(loose)
  // Nothing in the way scrolls, so the window does.
  expect(scrollParent(loose)).toBeNull()
})
