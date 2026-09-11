import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { Avatar } from './Avatar'

test('without a picture it shows the first letter', () => {
  const { container } = render(<Avatar email="dana@h3.local" />)
  expect(container).toHaveTextContent('d')
  expect(container.querySelector('img')).toBeNull()
})

test('with a picture it shows the picture', () => {
  render(<Avatar email="dana@h3.local" url="/api/avatar/u?v=1" alt="Dana" />)
  expect(screen.getByRole('img', { name: 'Dana' })).toHaveAttribute('src', '/api/avatar/u?v=1')
})

test('a picture that fails to load falls back to the letter', () => {
  const { container } = render(<Avatar email="omer@h3.local" url="/api/avatar/u?v=2" />)
  fireEvent.error(container.querySelector('img')!)
  expect(container.querySelector('img')).toBeNull()
  expect(container).toHaveTextContent('o')
})
