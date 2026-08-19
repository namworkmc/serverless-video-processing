# BMAD Report Delivery — shared on_complete handler

Every BMAD workflow in this repo MUST deliver its final report to Discord before exiting.
This file is the single source of truth for delivery; per-skill TOML overrides only point here.
To change delivery behavior, edit THIS file only.

## Configuration (resolved at runtime via the Hermes CLI — nothing hardcoded)

Each developer's Hermes install defines the delivery target and mentions in their
own Hermes env file. Resolve it generically with the Hermes CLI:

```
ENV_PATH=$(hermes config env-path)
DISCORD_ALLOWED_CHANNELS=$(grep '^DISCORD_ALLOWED_CHANNELS=' "$ENV_PATH" | cut -d= -f2-)
DISCORD_ALLOWED_USERS=$(grep '^DISCORD_ALLOWED_USERS=' "$ENV_PATH" | cut -d= -f2-)
```

- `DISCORD_ALLOWED_CHANNELS` — comma-separated channel IDs the bot may post to
- `DISCORD_ALLOWED_USERS` — comma-separated user IDs to mention in the report

### Channel selection rule

- **Exactly 1 allowed channel** → use it as the target: `discord:<channel_id>`.
- **More than 1 allowed channel** → ASK THE USER which channel to deliver to
  (list the allowed channel IDs); do not guess.
- **Empty or missing** → tell the user delivery is not configured and stop.

### Mention rule

The report message MUST begin with a mention of every allowed user, formatted as
Discord user mentions: `<@USER_ID>` for each ID in `DISCORD_ALLOWED_USERS`,
space-separated, followed by a newline before the report body.

## Delivery steps (mandatory, in order)

1. **Resolve target + mentions** per the Configuration rules above.

2. **Compose the report** — a concise completion summary containing:
   - What the workflow produced (artifact names + absolute paths)
   - Key results, decisions, or findings as short bullets
   - The next recommended step, if the workflow named one

3. **Size check** — Discord messages cap at 2000 characters. If the body
   (mentions included) would exceed ~1800 characters, keep the message body as a
   short summary and attach the full report file instead (see step 4).

4. **Send** via terminal command:

   ```
   hermes send --to discord:<channel_id> --subject "[<skill-name>] serverless-video-processing" "<mentions line>
   <report body>"
   ```

   To attach a report/artifact file, append a final line `MEDIA:<absolute-file-path>`
   to the message body.

5. **Verify** — the command must print `sent` and exit 0. On failure, retry once.
   If the retry also fails, tell the user delivery failed and why.
   Never exit silently without attempting delivery.
