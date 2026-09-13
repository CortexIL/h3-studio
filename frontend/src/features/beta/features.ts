/** The beta catalogue: what the model can do, what the studio exposes, and how to use it.
 *
 * One entry per feature. A phase ships by flipping `status` and filling `howTo`;
 * the page renders whatever is here, so the explanation and the feature never
 * drift apart.
 */
export type BetaStatus = 'available' | 'experimental' | 'planned'

export interface BetaFeature {
  id: string
  title: string
  status: BetaStatus
  /** One sentence: what it is. */
  summary: string
  /** What the model does underneath, in plain words. */
  why: string
  /** Step-by-step, once it is available. */
  howTo: string[]
  /** Honest limits. */
  limits: string[]
  /** Where in the studio it lives. */
  where?: string
}

export const BETA_FEATURES: BetaFeature[] = [
  {
    id: 'sound',
    title: 'Sound direction',
    status: 'available',
    where: 'Studio, under the Sound switch',
    summary: 'Tell the model what the clip should sound like: ambience, effects, music.',
    why: 'H3 generates the soundtrack in the same pass as the picture. It reads a separate soundscape and music description far better than sound words buried in the shot description.',
    howTo: [
      'Turn the Sound switch on; two fields appear under it.',
      'Soundscape: what the place sounds like - wind, birds, footsteps, a lantern creaking. Concrete sounds, in order of importance.',
      'Music: style, mood, instruments, tempo - "slow solo piano, warm, no vocals". Leave it empty for no music.',
      'Pick Draft or Final. The fields travel with the clip and come back with "Use again".',
    ],
    limits: [
      'Turbo (4 steps) produces noise instead of sound - use Draft or Final for real audio.',
      'Speech is generated in 11 languages; Hebrew is not one of them. Bring your own voice track instead (see Lip-sync).',
    ],
  },
  {
    id: 'shots',
    title: 'Multi-shot clips',
    status: 'planned',
    summary: 'Several shots with cuts inside one render, up to 15 seconds.',
    why: 'The model understands "SHOT 1 … cut to SHOT 2" in a prompt and edits inside the clip: consistent world, matching colour, one soundtrack across the cuts.',
    howTo: [],
    limits: ['Each shot needs a second or two; more than four shots in 15 seconds gets rushed.'],
  },
  {
    id: 'keyframes',
    title: 'Keyframes anywhere',
    status: 'planned',
    summary: 'Pin an image at any moment of the clip, not just the first or last frame.',
    why: 'The guide node anchors a frame at any index of the video. Start, middle and end can each be pinned, and several can be chained.',
    howTo: [],
    limits: ['Every pinned frame is a hard constraint - contradictory frames produce a jump, not a blend.'],
  },
  {
    id: 'controls',
    title: 'Full controls',
    status: 'available',
    where: 'Studio → Advanced controls',
    summary: 'Steps, motion strength, resolution, length, seed - every knob the graph has, editable per clip.',
    why: 'The graph exposes the sampler steps, a motion/structure shift for video and audio, the canvas size and the frame count. Presets are just saved combinations.',
    howTo: [
      'Open "Advanced controls" under Quality. Blank fields mean the preset\'s own value.',
      'Steps: more steps, more detail and more time - 20 is a fast draft, 30 is Final, 40+ is diminishing. Turbo is distilled for 4 and ignores the point of more.',
      'Motion (video shift, default 12): lower keeps the picture close to the frame and calm; higher lets the camera and the scene move and change more. Try 8 for title cards, 16–20 for action.',
      'Audio shift (default 3): the same idea for the soundtrack. Leave it unless the sound feels static.',
      'Render size: the preset\'s, 1920×1088 (experimental) or any multiple of 32 up to 2.1 megapixels. Bigger is slower in proportion to the pixels.',
      'Seed: a number pins the randomness so a prompt change is the only change between two clips. Blank = random. Ignored for several takes.',
    ],
    limits: ['More steps and more pixels cost time roughly in proportion. Turbo is a 4-step distillation and ignores the step count.'],
  },
  {
    id: 'hires',
    title: 'Native 1080p (experimental)',
    status: 'experimental',
    where: 'Quality → 1080p · experimental, or Advanced controls → Render size',
    summary: 'Render at 1920×1088 straight from the model.',
    why: 'The nodes accept any size in steps of 32. The model was trained on a 768-pixel short edge, so bigger canvases are outside what it learned - it may hold up, it may drift.',
    howTo: [
      'Pick the "1080p · experimental" quality, or set a custom render size in Advanced controls.',
      'Start with a 5-second clip: it costs about three times a native one.',
      'Compare it with the same seed at the native size before spending more.',
    ],
    limits: ['About 3× the render time and memory of the native canvas.', 'If it runs out of GPU memory the preset is removed.'],
  },
  {
    id: 'upscale',
    title: 'Upscale 2× / 4×',
    status: 'planned',
    summary: 'Real 1080p and 4K from a native render, for stills and finished clips.',
    why: 'A Real-ESRGAN pass inside the same ComfyUI: the video model renders at its native size, the upscaler adds the pixels. This is the reliable route to high resolution.',
    howTo: [],
    limits: ['Sharpens detail the model drew; it does not repair garbled text.', 'A clip is upscaled frame by frame - about a minute per clip at 2×.'],
  },
  {
    id: 'lipsync',
    title: 'Lip-sync from your audio',
    status: 'planned',
    summary: 'Upload a voice recording; the clip speaks it.',
    why: 'The guide node accepts an audio clip at frame 0. The model syncs mouth, timing and expression to the sound it is given - any language, since the words come from you.',
    howTo: [],
    limits: ['One speaker per clip works best.', 'The recording is trimmed to the clip length.'],
  },
  {
    id: 'ref2v',
    title: 'Reference to video',
    status: 'planned',
    summary: 'Up to 9 images, 3 videos and 3 audio clips as references: the same character, product or voice across clips.',
    why: 'A second checkpoint (Ref2VA) conditions on references you tag as <Picture 1>, <Video 1>, <Audio 1> in the prompt. It is a different 21 GB model, so the pod swaps models between jobs.',
    howTo: [],
    limits: ['Adds a 21 GB download to every pod boot.', 'Swapping models costs a minute or two between reference jobs and normal jobs.'],
  },
  {
    id: 'sage',
    title: 'Faster rendering (Sage Attention)',
    status: 'planned',
    summary: 'About a quarter off Final render times, same output.',
    why: 'An attention kernel patch on the pod. No UI - it is either on for every render or off.',
    howTo: [],
    limits: ['Needs the kernel to build on the pod image; if it fails to load, rendering falls back to the stock path.'],
  },
]
