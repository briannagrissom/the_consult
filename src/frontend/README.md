# Frontend

Vite + React + TypeScript client for _The Consult_. Renders answers with citations,
evidence filters, and multi-turn conversation history. Styled with Tailwind and shadcn-ui.

## Run locally

```bash
npm install
npm run dev -- --host --port 8080
```

Open `http://localhost:8080`. The UI expects the API at `VITE_API_BASE_URL`
(`.env`, defaults to `http://localhost:8081`), so start `src/llm-api` first.

Alternatively, `./docker-shell.sh` runs it in a container.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Production build |
| `npm run build:dev` | Development-mode build |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint |

No test suite is configured yet.

## Lovable

This project was scaffolded with [Lovable](https://lovable.dev/projects/32a62096-2fbf-425a-b544-675c83e29d1a).
Changes made there commit back to this repo automatically, and local pushes are reflected
in Lovable. To deploy from Lovable: Share → Publish. Custom domains are configured under
Project → Settings → Domains.
