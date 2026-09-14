/** What the classic layout keeps from the draft, and what it sends.
 *
 * The Create panel as it was before the beta: the same four modes, the old
 * names, the Sound switch, and nothing the beta added. A clip made there
 * renders exactly as it did then, because the request carries only what the
 * panel shows - whatever the draft still holds from the new panel.
 */
import type { Mode, NewJobsBody } from '@/api/types'
import type { Key } from '@/i18n'

import type { Block, Draft } from './composeStore'
import { blockedBy, toPayload } from './composeStore'

/** The modes the studio offered before the beta, in the order it offered them. */
export const CLASSIC_MODES: Mode[] = ['i2v', 't2v', 'flf2v', 'extend']

/** Qualities the beta added. Classic shows the ones that were there. */
export const NOT_CLASSIC_PRESETS = new Set(['balanced', 'sharp'])

export const BLOCK_KEY: Partial<Record<Block, Key>> = {
  uploading: 'classic.block.uploading',
  'upload-failed': 'classic.block.uploadFailed',
  'start-frame': 'classic.block.startFrame',
  'end-frame': 'classic.block.endFrame',
  'extend-source': 'classic.block.extendSource',
}

/** The draft as classic reads it: no shots, no fine-tuning, no keyframes. */
function classicDraft(s: Draft): Draft {
  return {
    ...s,
    split: s.split === 'shots' ? 'single' : s.split,
    keyframes: [],
    audio: null,
    effects: [],
    sound: '',
    music: '',
    steps: null,
    shiftVideo: null,
    shiftAudio: null,
    width: null,
    height: null,
    seed: null,
  }
}

/** The pre-beta request. Nothing the beta added travels with it. */
export function classicPayload(s: Draft): NewJobsBody {
  const full = toPayload(classicDraft(s))
  return {
    prompts: full.prompts,
    split: full.split,
    seconds: full.seconds,
    preset: full.preset,
    mode: full.mode,
    count: full.count,
    keep_audio: full.keep_audio,
    ref_images: full.ref_images,
  }
}

/** What stops the button here. The beta's own reasons cannot arise. */
export function classicBlockedBy(s: Draft): Block | null {
  return blockedBy(classicDraft(s))
}
