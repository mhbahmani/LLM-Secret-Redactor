# LiteLLM Proxy (OpenRouter)

Local LiteLLM proxy that forwards to OpenRouter with full request/response visibility.

## Setup

1. Copy `.env.example` to `.env`.
2. Put your real key in `.env`:

   ```
   OPENROUTER_API_KEY=sk-or-v1-...
   ```

3. Start:

   ```
   docker compose up -d
   ```

## Use

- Proxy endpoint: `http://localhost:4000`
- Auth header: `Authorization: Bearer <LITELLM_MASTER_KEY>` (default `sk-litellm-master-key`)
- Model names (config.yaml): `deepseek-v4-flash`, `deepseek-v4-pro`, `gemini-3.8-flash`
- Compatible with any OpenAI SDK: point `baseURL` at `http://localhost:4000/v1`.

## See what it sends

- **Console**: `docker compose logs -f litellm` — runs with `--detailed_debug`, prints the full request body (messages, params) sent to OpenRouter.
- **Admin UI**: `http://localhost:4000/ui` — request/response history + spend, in Postgres (`litellm-db-1`).

## Once set up

1. Start: `docker compose up -d`
2. Stop: `docker compose down`
3. Wipe data: `docker compose down -v`