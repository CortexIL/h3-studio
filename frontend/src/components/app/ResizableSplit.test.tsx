import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'

import { ResizableSplit } from './ResizableSplit'

beforeEach(() => localStorage.clear())

const divider = () => screen.getByRole('separator', { name: 'Resize the create panel' })

test('starts from the saved width, and ignores a saved width out of range', () => {
  localStorage.setItem('h3.leftWidth', '500')
  const { unmount } = render(<ResizableSplit left="L" right="R" />)
  expect(divider()).toHaveAttribute('aria-valuenow', '500')
  unmount()
  localStorage.setItem('h3.leftWidth', '9999')
  render(<ResizableSplit left="L" right="R" />)
  expect(divider()).toHaveAttribute('aria-valuenow', '380')
})

test('arrow keys move it, clamp at the edges, and remember the result', () => {
  render(<ResizableSplit left="L" right="R" />)
  fireEvent.keyDown(divider(), { key: 'ArrowRight' })
  expect(divider()).toHaveAttribute('aria-valuenow', '396')
  expect(localStorage.getItem('h3.leftWidth')).toBe('396')
  fireEvent.keyDown(divider(), { key: 'Home' })
  expect(divider()).toHaveAttribute('aria-valuenow', '280')
  fireEvent.keyDown(divider(), { key: 'ArrowLeft' })
  expect(divider()).toHaveAttribute('aria-valuenow', '280')
})

test('double-click puts it back and forgets the saved width', () => {
  localStorage.setItem('h3.leftWidth', '600')
  render(<ResizableSplit left="L" right="R" />)
  fireEvent.doubleClick(divider())
  expect(divider()).toHaveAttribute('aria-valuenow', '380')
  expect(localStorage.getItem('h3.leftWidth')).toBeNull()
})
