# Security

Anime Fortress is an administrative tool. It holds a Telegram bot token, can
post to channels, and downloads media from the network. Treat a deployment as
production infrastructure.

## Bot token

- Create the bot with [@BotFather](https://t.me/BotFather) and store the token
  in the environment (or in a secret manager injected as an environment
  variable).
- Never commit `.env`. It is already in `.gitignore`; `.env.example` contains
  placeholders only.
- Never paste a token into a chat, an issue, a log, or a source configuration.
  The bot token is a `SecretStr`, so it is masked in logs and repr output.
- If a token leaks, revoke it with BotFather (`/revoke`) and issue a new one.
- Give the bot the smallest set of privileges: it only needs to send messages
  in the channels you administer.

## Access control

- Only user IDs listed in `ADMIN_USER_IDS` can use the bot. Every
  administrative router is filtered by `IsAdmin`; non-admins receive an access
  denied message and cannot trigger any state change.
- The bot is intended to be used in private chats. Group usage is not
  supported: the FSM state is keyed by chat, so a group chat would leak
  context between users. Keep the bot in a private chat with the
  administrators.
- `ADMIN_USER_IDS` is parsed as numbers; an empty value disables all admin
  access. The `doctor` command warns when it is empty.

## Source and network safety

- Only configure sources you are authorized to download and publish. This is a
  legal requirement, not a technical one.
- URLs are validated: only `http`/`https`, no credentials in the URL, and no
  private, loopback, or link-local addresses unless `ALLOW_PRIVATE_NETWORKS`
  is explicitly enabled. This blocks SSRF against your internal network and
  cloud metadata endpoints.
- Feeds are parsed with `defusedxml`, so entity expansion and external entity
  attacks are rejected.
- Downloads are capped by `MAX_DOWNLOAD_MB` and time-limited by
  `HTTP_TIMEOUT_SECONDS`.
- Local library sources are restricted to directories under
  `MEDIA_LIBRARY_ROOTS`; the check is applied after path resolution so
  traversal and symlinks cannot escape the allowlist.
- Set `ALLOW_PRIVATE_NETWORKS=false` unless a source genuinely lives on a
  trusted private network.

## Credentials for sources

- Keep source credentials in environment variables and reference them by name
  with `headers_env`. The value is a JSON object of HTTP headers.
- The environment variable is read by the worker at request time; it is never
  written to the database, so a database backup does not leak source
  credentials.
- Rotate source credentials together with the bot token.

## Database and filesystem

- The database holds metadata, queue state, and upload history. It does not
  hold secrets, but it does hold the catalogue of what you publish.
- Restrict file permissions on `DATA_DIR`: it contains posters, rendered
  media, and the SQLite database.
- Backups contain the full catalogue and the file names you publish. Encrypt
  backups and restrict who can read them.
- The `audit_log` table records administrative actions; review it periodically
  and include it in backups.

## Secrets in logs

- `REDACT_SECRETS_IN_LOGS` (default `true`) removes tokens and authorization
  headers from log output.
- Source URLs are sanitized before they are logged or shown in the bot, so
  query strings with tokens are not leaked into chat messages.
- If you enable `LOG_JSON`, review what your HTTP client logs before shipping
  the logs to a third party.

## Deployment hardening

- Run the container as a non-root user (the provided image already does).
- Keep the bot on an internal network or behind a firewall; only the Bot API
  outbound connection is required.
- Restrict access to the machine that holds the media directory.
- Use a dedicated database role with only the permissions Anime Fortress
  needs; it performs migrations itself, so the role needs DDL rights — run
  migrations separately with a privileged role if that is a problem.
- Update dependencies regularly (`uv lock --upgrade`, then re-run the tests).

## Copyright and platform compliance

- Uploading copyrighted video to Telegram may violate copyright law, the
  Telegram Terms of Service, or both, even when the source is technically
  accessible.
- Do not use this tool to distribute content you have no right to distribute.
- Keep records of the licenses or permissions you rely on for each source.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the repository owner instead of
opening a public issue. Include a description, affected versions, and steps to
reproduce. Do not include live tokens or credentials in the report.
