import { expect, test } from 'vitest'

import { bandFrom, idsInBand, overlaps, rangeBetween } from './selection'

const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom })

test('a range covers everything between the two clips, whichever was clicked first', () => {
  const ids = ['a', 'b', 'c', 'd']
  expect(rangeBetween(ids, 'b', 'd')).toEqual(['b', 'c', 'd'])
  expect(rangeBetween(ids, 'd', 'b')).toEqual(['b', 'c', 'd'])
  expect(rangeBetween(ids, 'c', 'c')).toEqual(['c'])
})

test('a range involving a clip that is no longer there selects nothing', () => {
  expect(rangeBetween(['a', 'b'], 'a', 'gone')).toEqual([])
})

test('touching counts as overlapping, sharing an edge does not', () => {
  expect(overlaps(box(0, 0, 10, 10), box(5, 5, 15, 15))).toBe(true)
  expect(overlaps(box(0, 0, 10, 10), box(10, 0, 20, 10))).toBe(false)
  expect(overlaps(box(0, 0, 10, 10), box(20, 20, 30, 30))).toBe(false)
})

test('the band takes every card it touches, not only those it swallows whole', () => {
  const boxes = [
    { id: 'top-left', box: box(0, 0, 100, 100) },
    { id: 'clipped-corner', box: box(90, 90, 200, 200) },
    { id: 'far-away', box: box(500, 500, 600, 600) },
  ]
  expect(idsInBand(boxes, box(10, 10, 120, 120))).toEqual(['top-left', 'clipped-corner'])
})

test('a band is the same rectangle dragged in any direction', () => {
  const upLeft = bandFrom({ x: 100, y: 100 }, 20, 30)
  expect(upLeft).toEqual({ left: 20, top: 30, right: 100, bottom: 100 })
  expect(bandFrom({ x: 20, y: 30 }, 100, 100)).toEqual(upLeft)
})
