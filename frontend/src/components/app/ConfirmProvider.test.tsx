import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { expect, test } from 'vitest'

import { renderWithProviders } from '@/test/render'

import { type ConfirmOptions, useConfirm } from './confirm'

function Harness(props: ConfirmOptions) {
  const confirm = useConfirm()
  const [answer, setAnswer] = useState('none')
  return (
    <>
      <button onClick={async () => setAnswer(String(await confirm(props)))}>ask</button>
      <output>{answer}</output>
    </>
  )
}

test('confirming resolves true', async () => {
  const user = userEvent.setup()
  renderWithProviders(<Harness title="Delete this clip?" confirmLabel="Delete" destructive />)
  await user.click(screen.getByRole('button', { name: 'ask' }))
  expect(await screen.findByRole('alertdialog', { name: 'Delete this clip?' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('true'))
})

test('cancelling resolves false', async () => {
  const user = userEvent.setup()
  renderWithProviders(<Harness title="Sure?" />)
  await user.click(screen.getByRole('button', { name: 'ask' }))
  await user.click(await screen.findByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('false'))
})

test('Escape resolves false', async () => {
  const user = userEvent.setup()
  renderWithProviders(<Harness title="Sure?" />)
  await user.click(screen.getByRole('button', { name: 'ask' }))
  await screen.findByRole('alertdialog')
  await user.keyboard('{Escape}')
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('false'))
})

test('a failing action keeps the dialog open and shows why', async () => {
  const user = userEvent.setup()
  renderWithProviders(
    <Harness
      title="Delete?"
      confirmLabel="Delete"
      action={() => Promise.reject(new Error('the file could not be deleted'))}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'ask' }))
  await user.click(await screen.findByRole('button', { name: 'Delete' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('The file could not be deleted')
  expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  // The page behind a modal is hidden from assistive tech while it is open.
  expect(screen.getByRole('status', { hidden: true })).toHaveTextContent('none')
})

test('a successful action closes the dialog and resolves true', async () => {
  const user = userEvent.setup()
  renderWithProviders(<Harness title="Delete?" confirmLabel="Delete" action={() => Promise.resolve()} />)
  await user.click(screen.getByRole('button', { name: 'ask' }))
  await user.click(await screen.findByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('true'))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})
