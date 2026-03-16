# Cardea

Proxy to allow AI agents to safely access services, without the need for credentials.

To do useful stuff, our companion AI agents often need access to external services, such as
gmail, github, telegram, ...

There are 2 important aspects to consider from a security standpoint:

1. Is it safe to grant them that access at all?
2. Is there a way to give the access without explicitly sharing credentials?

Point 1 is a personal decision. A safe approach is typically to create dedicated accounts for the bots,
rather than giving them access to our own personal account. Clearly this comes with trade-offs and everybody
should think this through given their own specific situation.

Cardea helps with point 2. If you give a Large Language Model (LLM) your credentials, nothing ensures that they
won't end up in the provider logs and/or leaked on the web. Despite the remarkable efforts of the frontier
labs when it comes to increasing LLM security, the prompt-injection risk will always remain as it is intrinsic
in the computational model of LLMs, where "code" and data are not separated.

So the only safe way it to not share credentials at all. But then how can the agent access the service?
Through a separate local proxy that handles the credentials. That's Cardea.

Cardea exposes local endpoints for, e.g., sending an email via gmail. The agent just calls the endpoint with
no auth. Then Cardea injects the credentials and submits the actual request to the gmail APIs.

## Running

### Directly with uv

Copy `config.toml.example` to `config.toml` and enable the modules you need.
Set the required credentials as environment variables (listed in `config.toml.example`), then:

```bash
uv run cardea --host 127.0.0.1 --port 8000
```

### As a container with docker / podman

Build the image from the repo root:

```bash
podman build -t cardea .
```

When running in a container, credentials can be provided as files under `/run/secrets/`
(e.g. via `podman secret` or `docker secret`) instead of environment variables.
Each module looks for its secret by name (e.g. `cardea_github_token`) — first
as a file in `/run/secrets/<name>`, then as an env var. See `config.toml.example` for the full list.

```bash
# Example with podman secrets
echo -n "ghp_..." | podman secret create cardea_github_token -
podman run --secret cardea_github_token -v ./config.toml:/app/config.toml:ro -p 8000:8000 cardea
```

Mount your `config.toml` into the container at `/app/config.toml`.

## Contributing

Contributions from coding agents are welcome too. Respecting the architecture is mandatory.

### Adding a new service

#### Config-driven (no code changes)

For simple REST API proxying, add a `[services.<name>]` section to `config.toml`:

```toml
[services.my-api]
prefix = "/my-api"
upstream = "https://api.example.com"
auth = { type = "bearer", secret = "my_api_token" }
```

Supported auth types: `bearer`, `basic`, `header`, `query`, `none`.

Then create the secret (`podman secret create my_api_token /path/to/token`) and restart.

#### Custom module (for complex logic)

For services requiring custom logic (OAuth2 token refresh, non-HTTP protocols,
multi-tenant routing), create a Python module in `src/cardea/proxies/` with a
router, PREFIX, and TAG.

### Browser credential manager

Cardea includes a CDP-based (Chrome DevTools Protocol) credential manager that
can auto-fill login forms in a remote Chromium instance without the AI agent
ever seeing the actual credentials. This is useful when an agent drives a
browser (e.g. via Vito's browser tool) and needs to log in to a website.

The browser module is loaded automatically when a `[browser]` section exists in
`config.toml` -- it does not need an entry in `[modules]`.

#### Configuration

The `[browser]` section sets the CDP connection:

| Key              | Description                                                     |
|------------------|-----------------------------------------------------------------|
| `cdp_endpoint`   | WebSocket URL of the Chromium CDP debugging port (e.g. `ws://vito:9222`) |

Each `[browser.sites.<name>]` section defines a site whose login form Cardea
can fill:

| Key           | Description                                                      |
|---------------|------------------------------------------------------------------|
| `url_pattern` | Substring matched against the domain/URL passed by the caller    |
| `secret`      | Name of the Podman/Docker secret containing credentials as JSON  |
| `fields`      | Array of `{ selector, key }` objects (CSS selector + JSON key)   |

The secret must be a JSON object whose keys match the `key` values in `fields`.
For example, `{"username": "alice", "password": "s3cret"}`.

#### Example

```toml
[browser]
cdp_endpoint = "ws://vito:9222"

[browser.sites.github]
url_pattern = "github.com/login"
secret = "browser_github"
fields = [
  { selector = "#login_field", key = "username" },
  { selector = "#password", key = "password" },
]
```

Then create the secret:

```bash
echo -n '{"username": "alice", "password": "s3cret"}' | podman secret create browser_github -
```

#### How it works

1. The caller sends `POST /browser/fill` with `{"domain": "github.com/login"}`.
2. Cardea matches the domain against `url_pattern` in the configured sites.
3. Loads the credential JSON from the named secret.
4. Connects to Chromium via CDP and fills each form field using
   `Runtime.evaluate`, dispatching `input` and `change` events.
5. Returns `{"status": "filled", "fields_filled": N}`.

Vito's browser tool calls this endpoint automatically when it needs to log in
to a configured site.

## Who's cardea

In the Roman tradition, Cardea is a deity protecting households from harmful spirits entering through doors.
Symbolism: the hinge (the mechanism that allows a door or gate to open and close), in Latin _cardo, cardinis_.
