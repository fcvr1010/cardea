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

## Browser credential manager

Cardea can auto-fill login forms in a remote Chromium instance via the
Chrome DevTools Protocol (CDP), so the AI agent never sees the actual
credentials.

This is useful when an agent controls a browser (e.g. for web scraping
or testing) and needs to log in to a site. Instead of passing credentials
to the agent, Cardea fills the form fields directly in the browser.

### How it works

1. The agent sends `POST /browser/fill {"domain": "github.com"}`.
2. Cardea looks up the matching site in `[browser.sites.*]`.
3. Loads the credential from Podman/Docker secrets.
4. Connects to Chromium via CDP and fills each configured form field.
5. Returns `{"status": "filled", "fields_filled": N}`.

### Configuration

Add a `[browser]` section and one `[browser.sites.<name>]` block per
site to `config.toml`:

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

| Key | Description |
|---|---|
| `cdp_endpoint` | WebSocket URL of the Chromium CDP debugging port |
| `url_pattern` | Substring matched against the `domain` field in the `POST /browser/fill` request body |
| `secret` | Name of the Podman/Docker secret containing the credentials |
| `fields` | List of `{selector, key}` pairs mapping CSS selectors to keys in the secret JSON |

The secret must be a JSON object whose keys match the `key` values in
`fields`:

```json
{"username": "my-user", "password": "my-pass"}
```

Create the secret and restart Cardea:

```bash
echo -n '{"username":"user","password":"pass"}' | podman secret create browser_github -
```

The module is loaded automatically when a `[browser]` section is present
in `config.toml` -- no entry in `[modules]` is needed.

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

## Who's cardea

In the Roman tradition, Cardea is a deity protecting households from harmful spirits entering through doors.
Symbolism: the hinge (the mechanism that allows a door or gate to open and close), in Latin _cardo, cardinis_.
