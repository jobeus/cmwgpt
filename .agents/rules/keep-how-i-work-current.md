---
trigger: always_on
---

1. `src/prompts/how_i_work.txt` is the bot's own description of its architecture, added to every mention's system prompt so it can explain how it works in the channel
2. whenever a change alters anything it describes (triggers, context/history, prompt layout, memory, models/providers/tools, output, commands, features, storage, deploy/restart), update it in the same change, and remove anything that's no longer true
3. keep it detailed first and terse second: it costs tokens on every mention, so state facts, no filler, and no secrets, keys, hostnames or ports
