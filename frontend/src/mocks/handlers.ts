import { delay, http, HttpResponse } from 'msw'

import type { NewJobsBody } from '@/api/types'

import * as data from './data'

// In-memory state, so actions taken in the page show up on the next poll.
const state = {
  jobs: data.makeJobs(),
  clips: data.makeClips(),
  users: data.makeUsers(),
  status: data.makeStatus(),
  admin: data.makeAdminStatus(),
}

const HUES = [18, 200, 262, 142, 36, 320, 190, 8, 96]

/** A drawn stand-in for a poster frame: a gradient with a soft vignette. */
function posterSvg(id: string): string {
  const n = [...id].reduce((a, c) => a + c.charCodeAt(0), 0)
  const h = HUES[n % HUES.length]!
  return `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="hsl(${h} 55% 32%)"/><stop offset="1" stop-color="hsl(${(h + 40) % 360} 60% 12%)"/>
    </linearGradient>
    <radialGradient id="v" cx=".5" cy=".45" r=".75"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>
  </defs>
  <rect width="640" height="360" fill="url(#g)"/>
  <circle cx="${120 + (n % 400)}" cy="${90 + (n % 150)}" r="70" fill="hsl(${h} 80% 70%)" opacity=".18"/>
  <rect width="640" height="360" fill="url(#v)"/>
</svg>`
}

// The signed-in person; their picture changes as the mock is used.
let currentMe = { ...data.me }

export const handlers = [
  http.post('/api/auth/login', async () => {
    await delay(300)
    return HttpResponse.json(currentMe)
  }),
  http.post('/api/auth/logout', () => HttpResponse.json({ ok: true })),
  http.get('/api/me', () => HttpResponse.json(currentMe)),
  http.post('/api/me/password', async ({ request }) => {
    await delay(400)
    const body = (await request.json()) as { current_password: string }
    if (body.current_password !== 'current-pass') {
      return HttpResponse.json({ detail: 'your current password is not right' }, { status: 400 })
    }
    return HttpResponse.json({ ok: true })
  }),
  http.post('/api/me/sign-out-everywhere', () => HttpResponse.json({ ok: true })),
  http.post('/api/me/avatar', async ({ request }) => {
    await delay(400)
    const file = (await request.formData()).get('file')
    if (!(file instanceof File)) return HttpResponse.json({ detail: 'no file' }, { status: 400 })
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ''
    for (const b of bytes) binary += String.fromCharCode(b)
    currentMe = { ...currentMe, avatar_url: `data:${file.type || 'image/png'};base64,${btoa(binary)}` }
    state.users = state.users.map((u) => (u.id === currentMe.id ? { ...u, avatar_url: currentMe.avatar_url } : u))
    return HttpResponse.json(currentMe)
  }),
  http.delete('/api/me/avatar', () => {
    currentMe = { ...currentMe, avatar_url: null }
    state.users = state.users.map((u) => (u.id === currentMe.id ? { ...u, avatar_url: null } : u))
    return HttpResponse.json(currentMe)
  }),

  http.get('/api/status', () => HttpResponse.json(state.status)),
  http.get('/api/jobs', () => HttpResponse.json({ jobs: state.jobs })),
  http.get('/api/jobs/:id', ({ params }) => {
    const job = state.jobs.find((j) => j.id === params.id)
    const clip = state.clips.find((c) => c.id === params.id)
    // Like the real server: a clip cleared from the feed is still readable, which the Archive's viewer needs.
    const found = job ?? (clip && { ...clip, status: 'done', started_at: clip.created_at, attempts: 1, error: null, queue_position: null })
    return found ? HttpResponse.json(found) : HttpResponse.json({ detail: 'no such job' }, { status: 404 })
  }),
  http.post('/api/jobs', async ({ request }) => {
    await delay(300)
    const body = (await request.json()) as NewJobsBody
    const prompts = body.split === 'lines' ? body.prompts.split('\n').filter((l) => l.trim()) : [body.prompts]
    const count = prompts.length * (body.count ?? 1)
    const t = Date.now() / 1000
    prompts.forEach((prompt, i) =>
      state.jobs.unshift({
        id: `j-new-${t}-${i}`, status: 'queued', prompt: prompt.trim(), ref_images: body.ref_images ?? [],
        seconds: body.seconds ?? 10, seed: null, mode: body.mode ?? 'i2v', preset: body.preset ?? 'final',
        keep_audio: body.keep_audio ?? null, effects: body.effects ?? [],
        sound: body.sound ?? null,
        music: body.music ?? null,
        steps: body.steps ?? null,
        shift_video: body.shift_video ?? null,
        shift_audio: body.shift_audio ?? null,
        width: body.width ?? null,
        height: body.height ?? null,
        keyframes: body.keyframes ?? [],
        audio: body.audio ?? null,
        ref_videos: body.ref_videos ?? [],
        ref_audios: body.ref_audios ?? [],
        source_job_id: null,
        upscale_factor: null,
        created_at: t, started_at: null, finished_at: null, attempts: 0, error: null, bytes: null,
        queue_position: state.status.queue.total_queued + i, video_url: null, poster_url: null,
      }),
    )
    return HttpResponse.json({ created: [], count })
  }),
  http.post('/api/jobs/:id/cancel', ({ params }) => {
    state.jobs = state.jobs.map((j) => (j.id === params.id ? { ...j, status: 'cancelled' } : j))
    return HttpResponse.json({ ok: true })
  }),
  http.post('/api/jobs/:id/retry', ({ params }) => {
    state.jobs = state.jobs.map((j) => (j.id === params.id ? { ...j, status: 'queued', error: null } : j))
    return HttpResponse.json({ ok: true })
  }),
  http.post('/api/jobs/:id/again', () => HttpResponse.json({ ok: true, job_id: 'j-again' })),
  http.post('/api/jobs/:id/upscale', () => HttpResponse.json({ ok: true, job_id: 'j-upscale', frames: 124 })),
  http.post('/api/jobs/again', async ({ request }) => {
    const { ids } = (await request.json()) as { ids: string[] }
    return HttpResponse.json({ queued: ids.length, created: ids.map((id) => `again-${id}`) })
  }),
  http.delete('/api/jobs/:id', ({ params }) => {
    const job = state.jobs.find((j) => j.id === params.id)
    state.jobs = state.jobs.filter((j) => j.id !== params.id)
    return HttpResponse.json({ ok: true, hidden: job?.status === 'done' })
  }),
  http.post('/api/jobs/clear-finished', () => {
    const before = state.jobs.length
    state.jobs = state.jobs.filter((j) => j.status === 'queued' || j.status === 'running')
    return HttpResponse.json({ removed: before - state.jobs.length })
  }),
  http.post('/api/estimate', async ({ request }) => {
    const body = (await request.json()) as NewJobsBody
    const prompts = body.split === 'lines' ? body.prompts.split('\n').filter((l) => l.trim()).length : 1
    const clips = Math.max(1, prompts) * (body.count ?? 1)
    const per = body.preset === 'turbo' ? 4.2 : body.preset === 'draft' ? 9 : 22
    return HttpResponse.json({
      clips, gpu: 'NVIDIA GeForce RTX 5090', rate_per_hour: 0.89, minutes_per_clip: per,
      render_minutes: per * clips, startup_minutes: 6, total_minutes: per * clips + 6,
      cost_usd: ((per * clips + 6) / 60) * 0.89, cost_per_clip_usd: (per / 60) * 0.89, confidence: 'estimated',
    })
  }),
  http.post('/api/upload', async () => {
    await delay(500)
    return HttpResponse.json({ key: `uploads/u-admin/mock-${Date.now()}.png`, name: 'ref.png' })
  }),
  http.post('/api/upload/refvideo', () =>
    HttpResponse.json({ key: `uploads/u-admin/ref-${Date.now()}.mp4`, name: 'ref.mp4' })),
  http.post('/api/upload/audio', () =>
    HttpResponse.json({ key: `uploads/u-admin/track-${Date.now()}.wav`, name: 'track.wav' })),
  http.post('/api/upload/video', async () => {
    await delay(900)
    return HttpResponse.json({ key: `uploads/u-admin/mock-${Date.now()}.mp4`, name: 'clip.mp4' })
  }),
  http.post('/api/jobs/:id/extend-source', async () => {
    await delay(600)
    return HttpResponse.json({
      key: `uploads/u-admin/tail-${Date.now()}.mp4`, name: 'tail.mp4',
      preset: 'final', seconds: 10,
    })
  }),
  http.post('/api/inbox/upload', async () => {
    await delay(700)
    return HttpResponse.json({ queued: 3, images: {}, missing_images: [] })
  }),

  http.get('/api/archive', ({ request }) => {
    const url = new URL(request.url)
    const q = url.searchParams.get('q')?.toLowerCase() ?? ''
    const preset = url.searchParams.get('preset')
    const mode = url.searchParams.get('mode')
    const clips = state.clips.filter(
      (c) => (!q || c.prompt.toLowerCase().includes(q)) && (!preset || c.preset === preset) && (!mode || c.mode === mode),
    )
    return HttpResponse.json({ clips, next_cursor: null })
  }),
  http.delete('/api/archive/:id', async ({ params }) => {
    await delay(400)
    state.clips = state.clips.filter((c) => c.id !== params.id)
    state.jobs = state.jobs.filter((j) => j.id !== params.id)
    return HttpResponse.json({ ok: true })
  }),
  http.get('/api/poster/:id', ({ params }) =>
    new HttpResponse(posterSvg(String(params.id)), { headers: { 'Content-Type': 'image/svg+xml' } }),
  ),
  http.get('/api/image/*', () =>
    new HttpResponse(posterSvg('ref'), { headers: { 'Content-Type': 'image/svg+xml' } }),
  ),

  http.get('/api/admin/users', () => HttpResponse.json({ users: state.users })),
  http.post('/api/admin/users', async ({ request }) => {
    const body = (await request.json()) as { email: string; role: 'user' | 'admin' }
    const user = { id: `u-${Date.now()}`, email: body.email, role: body.role, is_active: true, token_version: 1, created_at: new Date().toISOString(), avatar_url: null, usage: { queued: 0, running: 0, done: 0, failed: 0, cancelled: 0, stored_bytes: 0, last_job_at: null } }
    state.users.push(user)
    return HttpResponse.json(user)
  }),
  http.patch('/api/admin/users/:id', async ({ params, request }) => {
    const patch = (await request.json()) as Partial<{ is_active: boolean; role: 'user' | 'admin' }>
    state.users = state.users.map((u) => (u.id === params.id ? { ...u, ...patch } : u))
    return HttpResponse.json(state.users.find((u) => u.id === params.id))
  }),
  http.get('/api/admin/status', () => HttpResponse.json(state.admin)),
  http.post('/api/admin/policy', async ({ request }) => {
    const { policy } = (await request.json()) as { policy: 'auto' | 'keep-warm' | 'off' }
    state.admin = { ...state.admin, policy }
    return HttpResponse.json({ policy })
  }),
  http.post('/api/admin/budget', async ({ request }) => {
    const { session_limit_usd } = (await request.json()) as { session_limit_usd: number }
    state.admin = { ...state.admin, session: { ...state.admin.session, limit_usd: session_limit_usd } }
    return HttpResponse.json({ session_limit_usd })
  }),
  http.post('/api/admin/runpod-key', async () => {
    await delay(600)
    return HttpResponse.json({ ok: true, hint: 'b7c1', restart_required: true, note: 'Redeploy or restart to use it.' })
  }),
  http.get('/api/admin/key-state', () => HttpResponse.json(data.keyState)),
  http.get('/api/admin/prompt-key-state', () => HttpResponse.json({ present: true, hint: 'mock' })),
  http.post('/api/admin/prompt-key', () => HttpResponse.json({ ok: true, hint: 'mock' })),
  http.post('/api/admin/sage', async ({ request }) => HttpResponse.json({ sage: ((await request.json()) as { enabled: boolean }).enabled })),
  http.post('/api/prompt/improve', async ({ request }) => {
    const body = (await request.json()) as { prompt: string; sound?: string; music?: string }
    await delay(700)
    return HttpResponse.json({
      description: `[Shot 1] Live-action, cinematic, ${body.prompt.trim()} The camera pushes in with small amplitude at slow speed.`,
      sounds: body.sound || 'Soft room tone with distant traffic.',
      music: body.music || '',
    })
  }),
  http.get('/api/admin/runs', () => {
    const runs = data.makeRuns()
    return HttpResponse.json({ runs, total_cost_usd: runs.reduce((a, r) => a + r.cost_estimate, 0) })
  }),
]
