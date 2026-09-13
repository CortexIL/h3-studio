import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect } from 'react'
import { toast } from 'sonner'

import { uploadWithProgress } from '@/api/client'
import { keys } from '@/api/keys'
import type { BatchResult, UploadResult } from '@/api/types'
import { errorMessage } from '@/lib/errors'

import type { RefTile, TileSlot } from './composeStore'
import { useCompose } from './composeStore'

const BATCH = /\.(zip|json|txt)$/i
const VIDEO = /\.(mp4|mov|m4v|webm)$/i
const AUDIO = /\.(mp3|wav|m4a|aac|ogg|flac|opus|aiff?)$/i

const isVideo = (file: File) => file.type.startsWith('video/') || VIDEO.test(file.name)
const isAudio = (file: File) => file.type.startsWith('audio/') || AUDIO.test(file.name)

// The file behind each tile and where it was sent, so a failed upload can be
// retried without the user finding it again. Not in the store: Files don't
// serialise.
const files = new Map<string, { file: File; url: string }>()

function forget(tile: RefTile | null | undefined) {
  if (!tile) return
  if (tile.previewUrl) URL.revokeObjectURL(tile.previewUrl)
  files.delete(tile.id)
}

export function useReferenceUploads() {
  const qc = useQueryClient()

  const upload = useCallback((id: string, file: File, url: string) => {
    const { updateTile } = useCompose.getState()
    files.set(id, { file, url })
    updateTile(id, { status: 'uploading', progress: 0, error: undefined })
    uploadWithProgress<UploadResult>(url, file, (p) => updateTile(id, { progress: p }))
      .then((r) => updateTile(id, { status: 'ready', key: r.key, progress: 1 }))
      .catch((err: unknown) => updateTile(id, { status: 'error', error: errorMessage(err) }))
  }, [])

  const place = useCallback(
    (file: File, slot: TileSlot) => {
      const state = useCompose.getState()
      // A frame slot holds one image, so putting a new one there drops the old.
      if (slot === 'start') forget(state.startFrame)
      if (slot === 'end') forget(state.endFrame)
      const id = crypto.randomUUID()
      state.addTile(slot, {
        id,
        name: file.name || 'Pasted image',
        status: 'uploading',
        progress: 0,
        previewUrl: URL.createObjectURL(file),
      })
      upload(id, file, '/api/upload')
    },
    [upload],
  )

  /** A video from the user's machine becomes the clip an extension continues. */
  const placeVideo = useCallback(
    (file: File) => {
      const state = useCompose.getState()
      forget(state.extendSource?.tile)
      const id = crypto.randomUUID()
      state.setExtendSource({
        from: 'upload',
        label: file.name || 'Uploaded video',
        tile: {
          id,
          name: file.name || 'video',
          status: 'uploading',
          progress: 0,
          previewUrl: URL.createObjectURL(file),
        },
      })
      upload(id, file, '/api/upload/video')
    },
    [upload],
  )

  /** A whole short video, or an audio clip, as a reference in References mode. */
  const placeReference = useCallback(
    (file: File, slot: 'refvideo' | 'refaudio') => {
      const id = crypto.randomUUID()
      useCompose.getState().addTile(slot, {
        id,
        name: file.name || (slot === 'refvideo' ? 'video' : 'audio'),
        status: 'uploading',
        progress: 0,
        previewUrl: slot === 'refvideo' ? URL.createObjectURL(file) : undefined,
      })
      upload(id, file, slot === 'refvideo' ? '/api/upload/refvideo' : '/api/upload/audio')
    },
    [upload],
  )

  /** A voice or audio track for the clip to follow. One per clip; a new one replaces it. */
  const placeAudio = useCallback(
    (file: File) => {
      const state = useCompose.getState()
      forget(state.audio)
      const id = crypto.randomUUID()
      state.setAudio({ id, name: file.name || 'audio', status: 'uploading', progress: 0 })
      upload(id, file, '/api/upload/audio')
    },
    [upload],
  )

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

  /**
   * Files from a picker, a drop or a paste.
   *
   * An upload never changes the mode. The old rule switched text-to-video to
   * image-to-video whenever a picture arrived, which worked while exactly one
   * mode took images; with several, a guess is right some of the time and a wrong
   * guess silently changes what the job is. The mode decides where a file goes,
   * and anything that does not fit is said out loud instead of being redirected.
   */
  const handleFiles = useCallback(
    (list: FileList | File[], slot?: TileSlot) => {
      const rejected: string[] = []
      const images: File[] = []
      const videos: File[] = []
      const audios: File[] = []
      for (const file of Array.from(list)) {
        if (BATCH.test(file.name)) uploadBatch(file)
        else if (isVideo(file)) videos.push(file)
        else if (isAudio(file)) audios.push(file)
        else if (file.type.startsWith('image/')) images.push(file)
        else rejected.push(file.name || 'file')
      }
      if (rejected.length) {
        toast.error(`${rejected.join(', ')} can't be used`, {
          description: 'Add images, a video, an audio track, a .zip, or a .txt / .json batch file.',
        })
      }

      const state = useCompose.getState()
      if (state.mode === 'r2v') {
        const roomV = Math.max(0, 3 - state.refVideos.length)
        const roomA = Math.max(0, 3 - state.refAudios.length)
        videos.slice(0, roomV).forEach((f) => placeReference(f, 'refvideo'))
        audios.slice(0, roomA).forEach((f) => placeReference(f, 'refaudio'))
        if (videos.length > roomV || audios.length > roomA) toast.info('References take up to 3 videos and 3 audio clips.')
        images.slice(0, Math.max(0, 9 - state.refs.length)).forEach((f) => place(f, 'refs'))
        if (images.length > 9 - state.refs.length) toast.info('References take up to 9 images.')
        return
      }
      if (audios.length) {
        if (state.mode === 'extend') {
          toast.error('Extend keeps the sound of the clip it continues.', {
            description: 'An audio track can be followed in every other mode.',
          })
        } else {
          placeAudio(audios[0]!)
          if (audios.length > 1) toast.info('Only the first audio file was used.')
        }
      }
      if (videos.length) {
        if (state.mode === 'extend') {
          placeVideo(videos[0]!)
          if (videos.length > 1) toast.info('Only the first video was used.')
        } else {
          toast.error('A video can only be used by Extend.', {
            description: 'Switch to Extend to continue it.',
          })
        }
      }
      if (!images.length) return

      // The keyframe button takes every image, each pinned at its own moment.
      if (slot === 'keyframe') {
        for (const image of images) place(image, 'keyframe')
        return
      }
      // A slot's own button was used: it takes one image and says so.
      if (slot === 'start' || slot === 'end') {
        place(images[0]!, slot)
        if (images.length > 1) toast.info(`Only the first image was used for the ${slot} frame.`)
        return
      }

      if (state.mode === 'flf2v') {
        const empty: TileSlot[] = []
        if (!state.startFrame) empty.push('start')
        if (!state.endFrame) empty.push('end')
        images.slice(0, empty.length).forEach((file, i) => place(file, empty[i]!))
        const spare = images.length - empty.length
        if (spare > 0) {
          toast.info(`Start to end uses two frames, so ${spare} image${spare === 1 ? ' was' : 's were'} not added.`)
        }
        return
      }

      images.forEach((file) => place(file, 'refs'))
      if (state.mode === 't2v') {
        toast.info('Text → video does not use images.', {
          description: 'They are kept for when you switch to Reference.',
        })
      } else if (state.mode === 'extend') {
        toast.info('Extend continues a video, so images are not used.', {
          description: 'They are kept for when you switch to Reference.',
        })
      }
    },
    [place, placeAudio, placeReference, placeVideo, uploadBatch],
  )

  const retry = useCallback(
    (id: string) => {
      const sent = files.get(id)
      if (sent) upload(id, sent.file, sent.url)
    },
    [upload],
  )

  const remove = useCallback((id: string) => {
    const s = useCompose.getState()
    forget([...s.refs, s.startFrame, s.endFrame, s.extendSource?.tile ?? null, s.audio, ...s.keyframes.map((k) => k.tile), ...s.refVideos, ...s.refAudios]
      .find((t) => t?.id === id) ?? null)
    s.removeTile(id)
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
