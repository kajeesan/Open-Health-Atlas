# Hermes Hevy integration

Use the restricted Hevy MCP gateway when a user asks, for example,
“Hermes, put this routine in Hevy”. The gateway supports bounded routine,
exercise-template, and routine-folder creation. The integration does not log completed workouts,
measurements, or arbitrary updates.

Search first with `mcp_hevy_search_routines`. If no exact normalized title is
present, validate the proposed structure and call `mcp_hevy_create_routine`.
Use the corresponding restricted creation tools for templates and folders.

The trusted gateway owns authentication. The assistant must never ask for an
API key, read credential files, place credentials in arguments, or reveal
provider responses containing secrets. Users supply their own Hevy account and
credential through the deployment environment described in `.env.example`.
