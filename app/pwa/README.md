# Home-screen icons

Served by `app/main.py` at `/pwa/*` — a static mount, no session required: a
phone fetches these before anyone signs in, and an installed icon keeps pointing
at the same URL, so these names must not change.

They are not `frontend/`'s assets. Vite hashes everything it builds, which is
right for code and wrong for an icon a phone has already saved.

## Why they are full-bleed

`web/logo.png` is a rounded square on transparency. iOS and Android apply their
own mask, so rounded corners baked into the file get masked twice and the
corners come out dark. These icons carry the gradient edge to edge and let the
phone round them.

`icon-maskable-512.png` is the same picture with the monogram pulled further in,
for launchers that crop an icon to a circle.

## Regenerating

From `web/logo.png` (1081×1081), fitting its gradient and re-laying the
monogram over a full-bleed copy of it. The script that did it is in
`docs/make-icons.py`; it needs only Pillow:

```bash
python docs/make-icons.py web/logo.png app/pwa
```
