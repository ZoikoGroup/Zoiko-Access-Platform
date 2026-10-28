# Zoiko Access — Admin

Admin console for Zoiko Access, built with Next.js 16 (App Router) and Tailwind CSS 4.

```bash
npm install
npm run dev      # http://localhost:3000  → redirects to /login
```

| Route | What it is |
|---|---|
| `/login` | Split-screen sign-in with Login / Register / Reset Password tabs |
| `/dashboard` | Neumorphic admin dashboard |
| `/dashboard/*` | Section pages linked from the sidebar (placeholders) |

## Where things live

- `src/app/globals.css` — brand tokens (`#6A3CF0`, `#243B73`), neumorphic shadows and utilities (`neu-card`, `neu-btn`, `neu-inset`, `gradient-border`, `shine`, `skeleton`), keyframes
- `src/components/auth/` — login hero panel and auth card
- `src/components/dashboard/` — shell, sidebar, topbar, stat cards, chart
- `src/lib/nav.ts` — sidebar menu
- `public/brand/` — logos

Login hero images (doctor, laptop, phone) live in `public/images/`.

Authentication is simulated (`AuthCard.tsx`, `enterDashboard`) — wire it to your auth provider before going live.
