# ArxivAgent Desktop

Electron + React + Vite desktop shell for the local FastAPI backend in the
repository root. Package management and scripts use Bun.

## Development

```bash
bun install
bun run dev
```

`dev` starts Vite on `http://localhost:5173` and launches Electron. The
desktop main process starts the Python backend on `http://127.0.0.1:7860`.

## Verification

```bash
bun run typecheck
bun run build
```

## Packaging

```bash
bun run build:canary
```

The Electron build copies the Vite view assets into the app package and the
Python backend files into Electron `resources/backend`. A packaged app still
needs a working Python runtime with the root `requirements.txt` dependencies
available on the target machine.
