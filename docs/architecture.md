# Architecture

This page is meant to answer four questions quickly:

1. **What are the main moving parts?**
2. **How does a mention turn into a reply?**
3. **Which services are wired together at startup?**
4. **How do the bot, database, and log viewer fit together in development?**

## Read this page like a map

| Section | Use it when you want to... |
| --- | --- |
| System architecture | Orient yourself to the whole stack |
| Service wiring at startup | See what `src/startup.py` actually constructs |
| Mention pipeline | Understand the primary request path |
| Downloader enrichment flow | Understand where transcript/article data comes from |
| Docker/runtime layout | Visualize the development stack |
| Component map | Jump straight to the file you likely need |

## Diagram: system architecture

What it shows:

- the Python bot as the center of Discord interaction
- OpenRouter/Runpod/downloader work feeding MariaDB logging
- the separate log-viewer backend/frontend path

```mermaid
flowchart LR
    classDef edge fill:#334155,color:#f8fafc,stroke:#475569,stroke-width:1px;
    classDef bot fill:#2563eb,color:#eff6ff,stroke:#1d4ed8,stroke-width:1.5px;
    classDef service fill:#0f766e,color:#ecfeff,stroke:#115e59,stroke-width:1.5px;
    classDef data fill:#7c3aed,color:#f5f3ff,stroke:#6d28d9,stroke-width:1.5px;
    classDef viewer fill:#ea580c,color:#fff7ed,stroke:#c2410c,stroke-width:1.5px;

    User((Discord users)):::edge --> Discord[Discord API / Gateway]:::edge

    subgraph BotApp[Python bot]
        Client[DiscordBotClient]:::bot
        Mention[MentionHandler]:::service
        Commands[Slash command modules]:::service
        Queue[QueueService]:::service
        State[StateService]:::service
        Dispatch[completion_dispatch<br/>model routing]:::service
        OpenAI[OpenAIService<br/>OpenRouter text]:::service
        Gemini[GeminiService<br/>native Gemini]:::service
        Runpod[RunpodService<br/>image draw/edit]:::service
        Downloader[Downloader pipeline]:::service
        MsgSvc[MessageService]:::service
        Restart[Restart + update services]:::service
    end

    Discord --> Client
    Client --> Mention
    Client --> Commands
    Mention --> Queue
    Mention --> Dispatch
    Dispatch --> OpenAI
    Dispatch --> Gemini
    Mention --> Downloader
    Mention --> State
    Mention --> MsgSvc
    Commands --> State
    Commands --> Runpod
    Commands --> MsgSvc
    Client --> Restart

    DB[(MariaDB<br/>request + pipeline logs)]:::data
    OpenAI --> DB
    Gemini --> DB
    Downloader --> DB
    Runpod --> DB
    Client --> DB

    subgraph Viewer[Log viewer]
        Backend[Node/TS backend<br/>API + Socket.IO]:::viewer
        Frontend[React/Vite frontend]:::viewer
        Browser((Browser)):::edge
    end

    Browser --> Frontend
    Frontend --> Backend
    Backend --> DB
```

## Diagram: service wiring at startup

What it shows:

- `main.py` -> `src/startup.py`
- the services created by `create_services()`
- how those services are injected into the Discord client

```mermaid
flowchart TB
    classDef core fill:#1d4ed8,color:#eff6ff,stroke:#1e40af,stroke-width:1.5px;
    classDef svc fill:#0f766e,color:#ecfeff,stroke:#115e59,stroke-width:1.2px;
    classDef aux fill:#7c3aed,color:#f5f3ff,stroke:#6d28d9,stroke-width:1.2px;

    Main[main.py]:::core --> Startup[src/startup.py]:::core
    Startup --> Config[load_config AppConfig]:::core
    Startup --> Factory[create_services]:::core
    Factory --> State[StateService]:::svc
    Factory --> Queue[QueueService]:::svc
    Factory --> OpenAI[OpenAIService]:::svc
    Factory --> Gemini[GeminiService]:::svc
    Factory --> Paste[PasteService]:::svc
    Factory --> Message[MessageService]:::svc
    Factory --> Runpod[RunpodService]:::svc
    Factory --> Restart[RestartHandler]:::aux
    Factory --> AutoUpdate[AutoUpdateService]:::aux
    Factory --> Announce[AnnouncementService]:::aux
    Factory --> Interject[InterjectService]:::aux
    Factory --> Death[DeathService]:::aux
    Factory --> Mention[MentionHandler]:::svc

    State --> Interject
    State --> Death
    State --> Mention
    Queue --> Mention
    OpenAI --> Interject
    OpenAI --> Mention
    Gemini --> Interject
    Gemini --> Mention
    Message --> Interject
    Message --> Mention

    Startup --> Client[DiscordBotClient]:::core
    Client --> Mention
    Client --> AutoUpdate
    Client --> Announce
    Client --> Interject
    Client --> Death
```

## Diagram: mention pipeline

This is the single most important runtime path in the app.

```mermaid
sequenceDiagram
    autonumber
    actor U as Discord user
    participant C as DiscordBotClient
    participant Q as QueueService
    participant M as MentionHandler
    participant S as StateService
    participant D as downloader_utils
    participant DP as completion_dispatch
    participant O as OpenAIService
    participant G as GeminiService
    participant MS as MessageService
    participant DB as MariaDB logger

    U->>C: Mention bot in channel
    C->>S: get_model(channel)
    C->>Q: queue_mention(message, bot_user, model)
    Q->>M: handle_mention(...)
    M->>S: mark_channel_active(channel)
    M->>M: build_chat_context (history, replies, embeds, attachments)
    M->>S: get_system_prompt(channel)
    M->>D: fetch_all_url_content(message text)
    D->>D: discover YouTube / TikTok / Twitter / Facebook / articles
    D->>DB: log pipeline steps and API calls
    D-->>M: aggregated enrichment block
    M->>DP: dispatch_completion(messages, model, system_prompt)
    alt model is google / google-high / hybrid
        DP->>G: get_chat_completion(...)
        G->>DB: log request / response metadata
        G-->>DP: reply content
    else other models
        DP->>O: get_chat_completion(...)
        O->>DB: log request / response metadata
        O-->>DP: reply content
    end
    DP-->>M: reply content
    M->>MS: send_channel_reply(...)
    MS-->>U: Discord reply
```

### What matters about this flow

- Mentions are **queued**, not processed in parallel ad hoc.
- Prompt context is more than just the latest message: it includes recent history, reply metadata, embeds, and attachments. The multimodal context array is built by `src/utils/chat_context.py` (`build_chat_context`), shared with the interject service.
- Model routing is centralized in `src/services/completion_dispatch.py`: `google`/`google-high` go to `GeminiService`, `hybrid` runs a two-phase Gemini-then-OpenRouter pipeline, and everything else goes to `OpenAIService`.
- URL enrichment happens **before** the LLM call and can materially change the prompt content.
- Logging is first-class: downloader and model activity both emit records consumed later by the log viewer.

## Diagram: downloader enrichment flow

```mermaid
flowchart LR
    classDef in fill:#334155,color:#f8fafc,stroke:#475569,stroke-width:1px;
    classDef src fill:#0f766e,color:#ecfeff,stroke:#115e59,stroke-width:1.2px;
    classDef cache fill:#7c3aed,color:#f5f3ff,stroke:#6d28d9,stroke-width:1.2px;
    classDef out fill:#ea580c,color:#fff7ed,stroke:#c2410c,stroke-width:1.2px;

    Msg[Incoming message text]:::in --> Discover[fetch_all_url_content]:::src
    Discover --> YT[YouTube transcript]:::src
    Discover --> TT[TikTok transcript]:::src
    Discover --> TW[Twitter/X context]:::src
    Discover --> FB[Facebook transcript]:::src
    Discover --> AR[Article extraction]:::src

    YT --> Cache[Persistent caches]:::cache
    TT --> Cache
    TW --> Cache
    FB --> Cache
    AR --> Cache

    YT --> Logs[Pipeline/API logging]:::cache
    TT --> Logs
    TW --> Logs
    FB --> Logs
    AR --> Logs

    Cache --> Aggregate[Aggregated injected text block]:::out
    Logs --> Aggregate
    Aggregate --> Prompt[Prompt sent to model]:::out
```

### Current enrichment sources

| Source | Helper | Notes |
| --- | --- | --- |
| YouTube | `src/utils/youtube_utils.py` | Transcript extraction with optional proxy assistance |
| TikTok | `src/utils/tiktok_utils.py` | Thin wrapper over `src/utils/media_transcribe.py` (yt-dlp download -> Groq transcription) |
| Twitter/X | `src/utils/twitter_utils.py` | Tweet/context lookup, with optional video transcription path |
| Facebook | `src/utils/facebook_utils.py` | Thin wrapper over `src/utils/media_transcribe.py` (yt-dlp download -> Groq transcription) |
| Generic articles | `src/utils/url_utils.py` | `httpx` fetch plus content extraction/fallbacks |
| Shared media pipeline | `src/utils/media_transcribe.py` | yt-dlp audio download + proxy fallback + Groq Whisper transcription, used by TikTok/Facebook |

## Diagram: docker/runtime layout

```mermaid
flowchart LR
    classDef ext fill:#334155,color:#f8fafc,stroke:#475569,stroke-width:1px;
    classDef ctr fill:#111827,color:#f9fafb,stroke:#374151,stroke-width:1.4px;
    classDef db fill:#7c3aed,color:#f5f3ff,stroke:#6d28d9,stroke-width:1.4px;

    Discord[Discord API]:::ext --> Bot[bot container<br/>python main.py]:::ctr
    Browser[Browser]:::ext --> Frontend[frontend container<br/>Vite dev server :5173]:::ctr
    Frontend --> Backend[backend container<br/>Node API + Socket.IO :3001]:::ctr
    Bot --> Maria[(db container<br/>MariaDB 11.7 :3306)]:::db
    Backend --> Maria

    Env[.env.development]:::ext --> Bot
    Env --> Backend
    Env --> Frontend
    SQL[init_db.sql]:::ext --> Maria
```

## Component map

| Path | Role | Open this when you need to... |
| --- | --- | --- |
| `main.py` | Boot entrypoint | See how process startup/shutdown begins |
| `src/startup.py` | Service factory | Understand the dependency graph |
| `src/config.py` | Config loading/defaults | Confirm env vars and defaults |
| `src/bot/client.py` | Discord client orchestration | See event handlers, command setup, service startup/shutdown |
| `src/bot/handlers/mention.py` | Main conversational path | Debug context assembly or mention behavior |
| `src/bot/commands/` | Slash commands | Inspect user-facing command behavior |
| `src/utils/chat_context.py` | Multimodal context builder | Change how history/embeds/attachments become the model input (shared by mention + interject) |
| `src/services/completion_dispatch.py` | Model routing | Understand hybrid/Gemini/OpenRouter selection (shared by mention + interject) |
| `src/services/openai_service.py` | Text-model integration | Understand OpenRouter calls |
| `src/services/gemini_service.py` | Native Gemini integration | Understand `google`/`google-high`/`hybrid` calls and the Gemini timeout |
| `src/services/runpod_service.py` | Image integration | Understand draw/edit requests |
| `src/services/state_service.py` | Per-channel persistence | See where model/system-prompt/service settings live |
| `src/services/queue_service.py` | FIFO work queue | Understand serialized mention processing |
| `src/db/logger.py` | Request/pipeline logging | Understand what reaches MariaDB |
| `src/db/connection.py` | DB pool wiring | Debug MariaDB connectivity |
| `log-viewer/backend/` | Log API service | Debug auth, socket streaming, DB reads |
| `log-viewer/frontend/` | Log UI | Debug frontend behavior and envs |

## End-to-end narrative

### Startup

1. `main.py` loads configuration and starts the app.
2. `src/startup.py` constructs the service graph.
3. `DiscordBotClient` receives those services, sets callbacks, registers commands, and boots Discord event handling.

### Mention reply

1. A user mentions the bot.
2. `DiscordBotClient` chooses the active model for the channel and queues the work.
3. `MentionHandler` gathers recent message history and reply context (via `build_chat_context`).
4. The handler enriches user text with downloader output for supported URLs.
5. `dispatch_completion` routes the assembled context to `OpenAIService` (OpenRouter), `GeminiService` (native Gemini), or the two-phase `hybrid` pipeline, based on the channel's model.
6. `MessageService` sends the final reply back to Discord.
7. Request and pipeline details are written to MariaDB.

### Observability

1. The log-viewer backend reads MariaDB logs and serves them over HTTP.
2. It also emits realtime updates over Socket.IO.
3. The React frontend authenticates, fetches recent logs, and subscribes to updates.

## Key architectural traits

- **Mention-first UX:** the primary conversational surface is normal Discord chat, not a slash command.
- **Service composition over globals:** `src/startup.py` explicitly wires the main collaborators.
- **Logging as a product feature:** MariaDB logging is not incidental; it powers a dedicated inspection UI.
- **Hybrid stack:** Python handles bot/runtime behavior, while Node/React handle log inspection.
