# Telegram gateway extension contract

OpenHealthAtlas does not bundle a Telegram poller. A compatible user-supplied gateway
can opt into the retained action worker through
`hermes_telegram_gateway_extension.py` without exposing the health CLI or its
state database directly.

The gateway adapter must:

1. import the extension from a reviewed, root-owned location;
2. call `register(adapter, application)` during application setup to register
   message-reaction handling;
3. pass callback-query updates to `handle_callback(adapter, update)` before its
   generic callback handler, but only the extension's `hx:` namespace is used;
4. provide `_is_callback_user_authorized(...)` compatible with the extension;
   verify that adapter interface separately from the action-worker unit tests
   (the pinned-runtime smoke is recorded in `VERIFICATION.md`); and
5. run the action worker as the unprivileged `hermes` service identity with a
   root-managed systemd credential and external state directory.

The installer deliberately does not patch, restart, or enable an external
gateway. Adapter versions and imports differ between providers, so the operator
must review that small integration point and exercise fictional callbacks and
reactions before activation.
