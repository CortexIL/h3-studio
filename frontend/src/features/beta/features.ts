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
    status: 'available',
    where: 'Studio → Prompt → Shots',
    summary: 'Several shots with cuts inside one render, up to 15 seconds.',
    why: 'The model understands "SHOT 1 … cut to SHOT 2" in a prompt and edits inside the clip: consistent world, matching colour, one soundtrack across the cuts.',
    howTo: [
      'Above the prompt box choose "Shots". Each card is one shot; the first opens on your reference image.',
      'For every later shot pick how it joins: a cut, a match cut (the same shape or motion carries across), or the same take with the camera moving on.',
      'Describe what each shot shows - the subject, the framing, the movement. The studio writes the SHOT 1 / SHOT 2 prompt for you.',
      'Give it enough seconds: the editor shows the time per shot and warns under 2.5 s.',
      'Sound direction applies to the whole clip - one soundtrack runs across the cuts.',
    ],
    limits: ['Each shot needs a second or two; more than four shots in 15 seconds gets rushed.'],
  },
  {
    id: 'keyframes',
    title: 'Keyframes anywhere',
    status: 'available',
    where: 'Studio → Keyframes, in every mode',
    summary: 'Pin an image at any moment of the clip, not just the first or last frame.',
    why: 'The guide node anchors a frame at any index of the video. Start, middle and end can each be pinned, and several can be chained.',
    howTo: [
      'Click "Add keyframe" and pick an image (or several - each becomes its own keyframe).',
      'Set the second it should appear at. It must sit inside the clip: the first frame belongs to the reference, the last to Start to end.',
      'Up to six, at least a quarter second apart. They are centre-cropped to the render size, so frame them like the reference.',
      'Describe the journey between them in the prompt; the model interpolates, it does not read minds.',
    ],
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
    title: 'Upscale 2× / 1080p',
    status: 'available',
    where: 'A finished clip → ⋯ menu, or the viewer',
    summary: 'Twice the render size, or an exact 1080p, from a finished clip.',
    why: 'A Real-ESRGAN pass inside the same ComfyUI: the video model renders at its native size, the upscaler adds the pixels. This is the reliable route to high resolution.',
    howTo: [
      'On a finished clip open the ⋯ menu (or the viewer) and choose Upscale 2× (2688×1536) or Upscale to 1080p (that, conformed to 1920×1080).',
      'A new job appears in the queue; the original stays as it is. Sound is carried across unchanged.',
      'Frame by frame in chunks of 32, so a 15-second clip takes a couple of minutes on the GPU.',
    ],
    limits: ['Sharpens detail the model drew; it does not repair garbled text.', 'A clip is upscaled frame by frame - about a minute per clip at 2×.'],
  },
  {
    id: 'lipsync',
    title: 'Lip-sync from your audio',
    status: 'available',
    where: 'Studio → Voice / audio track (every mode but Extend)',
    summary: 'Upload a voice recording; the clip speaks it.',
    why: 'The guide node accepts an audio clip at frame 0. The model syncs mouth, timing and expression to the sound it is given - any language, since the words come from you.',
    howTo: [
      'Record or export the line as mp3, wav, m4a, aac, ogg or flac. Up to 30 MB; it is re-encoded and cut to the clip length.',
      'Click "Add audio" (or drop the file on the composer). The Sound switch turns on by itself - the track would be stripped otherwise.',
      'Use a Reference frame of the speaker and say so in the prompt: "she speaks to camera, calm, small natural gestures".',
      'Give the clip at least as many seconds as the line. Draft or Final for real sound; Turbo renders noise.',
    ],
    limits: ['One speaker per clip works best.', 'The recording is trimmed to the clip length.'],
  },
  {
    id: 'ref2v',
    title: 'Reference to video',
    status: 'available',
    where: 'Studio → Mode → References',
    summary: 'Up to 9 images, 3 videos and 3 audio clips as references: the same character, product or voice across clips.',
    why: 'A second checkpoint (Ref2VA) conditions on references you tag as <Picture 1>, <Video 1>, <Audio 1> in the prompt. It is a different 21 GB model, so the pod swaps models between jobs.',
    howTo: [
      'Pick the References mode. Add images (a face, a product, a style frame), short videos (a motion, a camera move - re-encoded to 15 s at 768 px) and audio clips (a voice).',
      'Write the prompt with the tags shown under the references, in that order: "<Picture 1> is the woman; keep her face. <Video 1> gives the camera move. <Audio 1> is her voice, use it exactly."',
      'Say what each reference drives - identity, style, motion, camera, voice. Ref2VA is sensitive to wording; precise tags work, vague ones drift.',
      'Turbo works here too, with its own 4-step LoRA. Keyframes and the audio-track slot are off in this mode - use references instead.',
    ],
    limits: ['Adds a 21 GB download to every pod boot.', 'Swapping models costs a minute or two between reference jobs and normal jobs.'],
  },
  {
    id: 'sage',
    title: 'Faster rendering (Sage Attention)',
    status: 'available',
    where: 'Automatic, on every render the pod can patch',
    summary: 'About a quarter off Final render times, same output.',
    why: 'An attention kernel patch on the pod. No UI - it is either on for every render or off.',
    howTo: [
      'Nothing to do. The pod installs SageAttention and the patch node while it boots; the studio asks the pod whether it has them and patches the model only when it does.',
      'Compare a clip rendered before and after with the same seed: the picture should match, the time should drop.',
    ],
    limits: ['Needs the kernel to build on the pod image; if it fails to load, rendering falls back to the stock path.'],
  },
]
