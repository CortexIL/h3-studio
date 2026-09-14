import { expect, test } from 'vitest'

import { useLayout } from './layout'

test('the new layout is the default and the switch remembers itself', () => {
  useLayout.setState({ layout: 'studio' })
  expect(useLayout.getState().layout).toBe('studio')
  useLayout.getState().toggle()
  expect(useLayout.getState().layout).toBe('classic')
  expect(JSON.parse(localStorage.getItem('h3.layout') ?? '{}').state.layout).toBe('classic')
  useLayout.getState().toggle()
  expect(useLayout.getState().layout).toBe('studio')
})
