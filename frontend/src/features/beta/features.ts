/** The beta catalogue: what the studio can do, said for someone who has never
 *  touched an AI tool. No model names, no node names, no numbers that only
 *  mean something to an engineer.
 *
 * One entry per feature. A phase ships by flipping `status` and filling `howTo`;
 * the page renders whatever is here, so the explanation and the feature never
 * drift apart.
 */
import { useLocale } from '@/i18n'

import { BETA_FEATURES_HE } from './features.he'

export type BetaStatus = 'available' | 'experimental' | 'planned'

export interface BetaFeature {
  id: string
  title: string
  status: BetaStatus
  /** One sentence: what you get. */
  summary: string
  /** What happens when you use it, in plain words. */
  why: string
  /** Step-by-step, once it is available. */
  howTo: string[]
  /** Honest limits, in plain words. */
  limits: string[]
  /** Where in the studio it lives. */
  where?: string
  /** Rendered on a real GPU and checked, not just built and unit-tested. */
  verified?: boolean
}

export const BETA_FEATURES: BetaFeature[] = [
  {
    id: 'sound',
    title: 'Describe the sound',
    status: 'available',
    where: 'Studio, under the Sound switch',
    summary: 'Tell the studio what the clip should sound like: the place, the effects, the music.',
    why: 'Every clip comes with its own soundtrack, made together with the picture. When you describe the sounds in their own fields instead of inside the shot description, the result matches what you asked for far more often.',
    howTo: [
      'Turn on the Sound switch. Two fields appear under it.',
      'Sounds: what you would hear standing there. Wind, birds, footsteps, a creaking lantern. Name real sounds, the most important first.',
      'Music: the style and the mood, for example "slow piano, warm, no singing". Leave it empty if you want no music.',
      'Choose the Best quality. Quick makes garbled sound.',
      'Both fields come back with "Use again", so you can reuse them on the next clip.',
    ],
    limits: [
      'The Quick quality gives noise instead of sound. Use Best when the sound matters.',
      'Spoken words come out in 11 languages, and Hebrew is not one of them. For Hebrew speech, record the line yourself and use "Voice from your recording".',
    ],
  },
  {
    id: 'shots',
    title: 'Several shots in one clip',
    status: 'available',
    where: 'Studio → Describe the clip → Several shots',
    summary: 'One clip that cuts between different shots, up to 15 seconds, like a short edited scene.',
    why: 'Instead of one continuous take, the clip can jump between shots: a wide view, then a close-up, then something else. The place, the look and the soundtrack stay the same across the cuts.',
    howTo: [
      'Above the description box choose "Several shots". Each card is one shot, and the first one starts from your image.',
      'For every shot after the first, choose how it joins the one before: a plain cut, a match cut (the same shape or movement carries across the cut), or the camera simply moving on without a cut.',
      'Describe what each shot shows: who or what, how close, what moves. The studio writes the full description for you.',
      'Give the clip enough seconds. The editor shows how long each shot gets and warns when a shot is under 2.5 seconds.',
      'The sound fields apply to the whole clip: one soundtrack runs across all the cuts.',
    ],
    limits: ['Each shot needs a second or two. More than four shots in 15 seconds feels rushed.'],
  },
  {
    id: 'keyframes',
    title: 'Images at chosen moments',
    status: 'available',
    where: 'Studio → Images at chosen moments, in every way of making a clip',
    summary: 'Make the clip pass through images of yours at the seconds you choose, not only at the start or the end.',
    why: 'Normally you give one image and the clip starts from it. Here you can also say "at second 3 show this, at second 6 show that", and the clip travels from one image to the next.',
    howTo: [
      'Click "Add an image" and pick one. Pick several and each gets its own moment.',
      'Type the second it should appear at. It has to be inside the clip: the very first moment belongs to your starting image, and the very last to "From a start and an end image".',
      'Up to six, at least a quarter of a second apart. They are cropped to fit the clip, so frame them like your starting image.',
      'Describe the journey between them in the description. The studio fills in the movement, but it cannot guess what you meant.',
    ],
    limits: ['Each image is a fixed point the clip must hit. Two images that contradict each other give a jump, not a smooth blend.'],
  },
  {
    id: 'controls',
    title: 'Fine-tuning',
    status: 'available',
    where: 'Studio → Fine-tuning',
    summary: 'Detail, motion, sound variation and a "same result again" number, each as a simple choice.',
    why: 'Quick and Best are just saved combinations of these settings. Opening them lets you trade waiting time for detail, calm the picture down or let it move more, and repeat a result exactly.',
    howTo: [
      'Open "Fine-tuning" under Quality. Every setting starts on the choice that matches the quality you picked.',
      'Detail: Quick, Normal or Extra. It is how long the studio works on every frame. More detail means more waiting, and above Extra nothing visibly improves. With the Quick quality this choice is off: it always uses its own fast setting.',
      'Motion: Calm, Normal or Lively. Calm keeps the picture close to your image and steady; Lively lets the camera and the scene move and change more. Calm suits title cards, Lively suits action.',
      'Sound variation: Normal or More varied. Leave it on Normal unless the sound feels flat.',
      'Same result again: every clip gets a number, shown in the viewer. Type it here to get the same result again and change only the description. Blank means a new random result. Ignored when you make several versions.',
      'Custom: each setting also takes a number of your own, for anyone who wants the exact value.',
    ],
    limits: [
      'More detail costs more waiting, roughly in proportion.',
      'The Quick quality always uses its own fast setting, whatever detail you choose.',
    ],
  },
  {
    id: 'upscale',
    title: 'Enlarge a finished clip',
    status: 'available',
    where: 'A finished clip → ⋯ menu, or the viewer',
    summary: 'Double the size of a finished clip, or bring it to exactly 1080p, without making it again.',
    why: 'The clip is made at its normal size, then every frame is enlarged and sharpened by a separate tool. This is the dependable way to get a big, sharp file.',
    howTo: [
      'On a finished clip open the ⋯ menu (or the viewer) and choose "Enlarge 2×" (2688×1536) or "Enlarge to 1080p" (the same, then fitted to 1920×1080).',
      'A new job appears in the queue. The original stays as it is, and its sound is copied over unchanged.',
      'It works frame by frame, so a 15-second clip takes a few minutes on the GPU.',
    ],
    limits: [
      'It sharpens what is already there. It cannot fix garbled text or wrong details.',
      'It counts as a new clip in the queue and on the bill: about a minute of GPU per clip at 2×.',
    ],
  },
  {
    id: 'lipsync',
    title: 'Voice from your recording',
    status: 'available',
    where: 'Studio → Voice recording (every way except "Continue a video")',
    summary: 'Upload a recording of someone speaking, and the person in the clip says it, mouth movements and all.',
    why: 'The studio listens to your recording and moves the face in time with it. Because the words come from your recording, it works in any language, Hebrew included.',
    howTo: [
      'Record or export the line as mp3, wav, m4a, aac, ogg or flac, up to 30 MB. The studio converts it and cuts it to the length of the clip.',
      'Click "Add a recording" (or drop the file onto the form). The Sound switch turns on by itself, because otherwise the voice would be thrown away.',
      'Use a starting image of the speaker and say so in the description, for example "she speaks to camera, calm, small natural gestures".',
      'Make the clip at least as long as the recording. Choose Best; Quick makes garbled sound.',
    ],
    limits: ['One speaker per clip works best.', 'The recording is cut to the length of the clip.'],
  },
  {
    id: 'ref2v',
    title: 'Keep the same person, product or voice',
    status: 'available',
    where: 'Studio → How to make it → Copy from examples',
    summary: 'Give the studio up to 9 images, 3 short videos and 3 sound clips, and tell it what to take from each: a face to keep, a look to copy, a camera move to repeat, a voice to use.',
    why: 'In the normal mode your image is only the first frame. In this mode your files are examples the studio keeps looking at while it makes the clip, so the same character, product or voice can appear across many clips.',
    howTo: [
      'Choose "Copy from examples". Add images (a face, a product, a look), short videos (a movement or a camera move; they are shortened to 15 seconds) and sound clips (a voice).',
      'Each file gets a tag, shown under the files: <Picture 1>, <Video 1>, <Audio 1>. Use the tags in the description, for example: "<Picture 1> is the woman; keep her face. <Video 1> gives the camera move. <Audio 1> is her voice, use it exactly."',
      'Say what each file is for. Precise wording works; vague wording drifts.',
      'Quick works here too. "Images at chosen moments" and the voice recording are off in this mode; use examples instead.',
    ],
    limits: [
      'This mode uses a second, separate model of 21 GB, so the GPU downloads more when it starts and takes a minute or two to switch between this mode and the others.',
    ],
  },
  {
    id: 'sage',
    title: 'Faster rendering',
    status: 'available',
    where: 'Automatic, on every clip the GPU can speed up',
    summary: 'About a quarter less waiting on the Best quality, with the same result.',
    why: 'A speed-up that is installed on the GPU while it starts. There is nothing to switch on: when the GPU has it, every clip uses it; when it does not, clips are made the usual way.',
    howTo: [
      'Nothing to do.',
      'To check it: make a clip with the same seed before and after. The picture should match and the time should drop.',
    ],
    limits: ['If the speed-up fails to install on the GPU, clips are simply made at the usual speed.'],
  },
]

/** The catalogue in the chosen language. Status and the GPU check always come
 *  from the English entry, so a translation can never claim more than the code does. */
export function betaFeatures(locale: 'en' | 'he'): BetaFeature[] {
  if (locale !== 'he') return BETA_FEATURES
  return BETA_FEATURES.map((f) => ({ ...f, ...BETA_FEATURES_HE[f.id] }))
}

export function useBetaFeatures(): BetaFeature[] {
  return betaFeatures(useLocale((s) => s.locale))
}
