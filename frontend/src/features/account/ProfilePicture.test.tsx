import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import type { Me } from '@/api/types'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'

import { AccountPage } from './AccountPage'

const uploads = vi.hoisted(() => [] as File[])
vi.mock('@/api/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/api/client')>()
  return {
    ...real,
    uploadWithProgress: vi.fn(async (_path: string, file: File) => {
      uploads.push(file)
      return { id: 'u-1', email: 'dana@h3.local', role: 'user', avatar_url: '/api/avatar/u-1?v=new' }
    }),
  }
})

let me: Me
let deletes = 0

beforeEach(() => {
  uploads.length = 0
  deletes = 0
  me = { id: 'u-1', email: 'dana@h3.local', role: 'user', avatar_url: null }
  // jsdom has no canvas; the crop step draws on one.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    drawImage: vi.fn(),
    imageSmoothingQuality: 'low',
  } as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (cb) {
    cb(new Blob(['png'], { type: 'image/png' }))
  })
  server.use(
    http.get('/api/me', () => HttpResponse.json(me)),
    http.delete('/api/me/avatar', () => {
      deletes++
      me = { ...me, avatar_url: null }
      return HttpResponse.json(me)
    }),
  )
})

afterEach(() => vi.restoreAllMocks())

async function loadPicked(width: number, height: number) {
  const img = (await screen.findByAltText('Picture to crop')) as HTMLImageElement
  Object.defineProperty(img, 'naturalWidth', { configurable: true, value: width })
  Object.defineProperty(img, 'naturalHeight', { configurable: true, value: height })
  fireEvent.load(img)
}

test('choosing a picture opens the crop step, and saving uploads a cropped PNG', async () => {
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />, { route: '/account' })
  expect(await screen.findByRole('button', { name: 'Upload picture' })).toBeInTheDocument()

  await user.upload(screen.getByTestId('avatar-input'), new File(['raw'], 'me.jpg', { type: 'image/jpeg' }))
  const dialog = await screen.findByRole('dialog', { name: 'Crop your picture' })
  const save = within(dialog).getByRole('button', { name: 'Save picture' })
  expect(save).toBeDisabled() // nothing to save until the picture has loaded

  await loadPicked(1200, 800)
  await user.click(save)
  await waitFor(() => expect(uploads).toHaveLength(1))
  expect(uploads[0]!.name).toBe('avatar.png')
  expect(uploads[0]!.type).toBe('image/png')
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Crop your picture' })).not.toBeInTheDocument())
})

test("a picture the browser can't open says so instead of saving", async () => {
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />, { route: '/account' })
  await user.upload(await screen.findByTestId('avatar-input'), new File(['raw'], 'me.heic', { type: '' }))
  const dialog = await screen.findByRole('dialog', { name: 'Crop your picture' })
  fireEvent.error(await screen.findByAltText('Picture to crop'))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent("can't be opened here")
  expect(within(dialog).getByRole('button', { name: 'Save picture' })).toBeDisabled()
})

test('a file that is not a picture is refused before the crop step', async () => {
  renderWithProviders(<AccountPage />, { route: '/account' })
  const input = await screen.findByTestId('avatar-input')
  fireEvent.change(input, { target: { files: [new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' })] } })
  expect(await screen.findByRole('alert')).toHaveTextContent("That isn't a picture")
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('removing the picture asks first', async () => {
  me = { ...me, avatar_url: '/api/avatar/u-1?v=1' }
  const user = userEvent.setup()
  renderWithProviders(<AccountPage />, { route: '/account' })
  await user.click(await screen.findByRole('button', { name: /Remove/ }))
  const dialog = await screen.findByRole('alertdialog', { name: 'Remove your profile picture?' })
  expect(deletes).toBe(0)
  await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(deletes).toBe(1))
  expect(await screen.findByRole('button', { name: 'Upload picture' })).toBeInTheDocument()
})
