import { expect, test } from 'vitest'

import { safeNext } from './next'

test.each([
  ['/archive', '/archive'],
  ['/archive?q=car', '/archive?q=car'],
  [null, '/'],
  ['', '/'],
  ['https://evil.example', '/'],
  ['//evil.example', '/'],
  ['/\\evil.example', '/'],
  ['javascript:alert(1)', '/'],
])('safeNext(%s) -> %s', (input, expected) => {
  expect(safeNext(input)).toBe(expected)
})
