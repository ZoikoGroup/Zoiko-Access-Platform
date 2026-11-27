# frontend/shared

Code every app uses, written once: `ui.tsx` (cards, tables, pills, icons, logo), `AuthShell.tsx`
(sign-in page frame, password field, Google button) and `auth.css`.

Each app reaches it as `@shared/...` (set up in its `vite.config.ts` and `tsconfig.json`), and keeps
a one-line `src/ui.tsx` / `src/AuthShell.tsx` that re-exports it, so pages import `./ui` as before.
No install step: the shared files use each app's own React.

Not shared yet because the copies differ: `useLoad.tsx` (polling default and a reload key),
`styles.css` (small per-app differences).
