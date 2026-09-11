import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'
import { toast } from 'sonner'

import { uploadWithProgress } from '@/api/client'
import { keys } from '@/api/keys'
import type { BatchResult, UploadResult } from '@/api/types'
import { errorMessage } from '@/lib/errors'

import { useCompose } from './composeStore'

const BATCH = /\.(zip|json|txt)$/i

// The File behind each tile, so a failed upload can be retried without the
// user finding the file again. Not in the store: Files don't serialise.
const files = new Map<string, File>()

export function useReferenceUploads() {
  const qc = useQueryClient()

  const upload = useCallback((id: string, file: File) => {
    const { updateRef } = useCompose.getState()
    updateRef(id, { status: 'uploading', progress: 0, error: undefined })
    uploadWithProgress<UploadResult>('/api/upload', file, (p) => updateRef(id, { progress: p }))
      .then((r) => updateRef(id, { status: 'ready', key: r.key, progress: 1 }))
      .catch((err: unknown) => updateRef(id, { status: 'error', error: errorMessage(err) }))
  }, [])

  const uploadBatch = useCallback(
    (file: File) => {
      const id = toast.loading(`Reading ${file.name}…`)
      uploadWithProgress<BatchResult>('/api/inbox/upload', file)
        .then((r) => {
          const queued = r.queued === 1 ? 'Queued 1 clip' : `Queued ${r.queued} clips`
          const missing = r.missing_images.length
          toast.success(`${queued} from ${file.name}`, {
            id,
            description: missing
              ? `${missing} image${missing === 1 ? " wasn't" : "s weren't"} in the file: ${r.missing_images.slice(0, 3).join(', ')}`
              : undefined,
          })
          void qc.invalidateQueries({ queryKey: keys.jobs })
          void qc.invalidateQueries({ queryKey: keys.status })
        })
        .catch((err: unknown) => toast.error(errorMessage(err), { id }))
    },
    [qc],
  )

  const handleFiles = useCallback(
    (list: FileList | File[]) => {
      const all = Array.from(list)
      const rejected: string[] = []
      let images = 0
      for (const file of all) {
        if (BATCH.test(file.name)) {
          uploadBatch(file)
        } else if (file.type.startsWith('image/')) {
          const id = crypto.randomUUID()
          files.set(id, file)
          useCompose.getState().addRef({
            id,
            name: file.name || 'Pasted image',
            status: 'uploading',
            progress: 0,
            previewUrl: URL.createObjectURL(file),
          })
          upload(id, file)
          images++
        } else {
          rejected.push(file.name || 'file')
        }
      }
      if (rejected.length) {
        toast.error(`${rejected.join(', ')} can't be used`, {
          description: 'Add images, a .zip, or a .txt / .json batch file.',
        })
      }
      // A reference on a text-to-video job would be silently ignored.
      if (images && useCompose.getState().mode === 't2v') {
        useCompose.getState().setMode('i2v')
        toast.info('Switched to Image → video, because you added a reference.')
      }
    },
    [upload, uploadBatch],
  )

  const retry = useCallback(
    (id: string) => {
      const file = files.get(id)
      if (file) upload(id, file)
    },
    [upload],
  )

  const remove = useCallback((id: string) => {
    const tile = useCompose.getState().refs.find((r) => r.id === id)
    if (tile?.previewUrl) URL.revokeObjectURL(tile.previewUrl)
    files.delete(id)
    useCompose.getState().removeRef(id)
  }, [])

  return { handleFiles, retry, remove, canRetry: (id: string) => files.has(id) }
}

/** Files pasted anywhere on the page become references. Plain text paste is left alone. */
export function useFilePaste(onFiles: (files: File[]) => void) {
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const pasted = Array.from(event.clipboardData?.files ?? [])
      if (!pasted.length) return
      event.preventDefault()
      onFiles(pasted)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [onFiles])
}
