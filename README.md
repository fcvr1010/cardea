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

Addition of new modules is welcome. Contributions from coding agents are welcome too. Respecting the
(extremely simple) architecture is mandatory.

Adding a new proxy is just creating a file in `src/cardea/proxies/` with a router, PREFIX, and TAG.

## Who's cardea

In the Roman tradition, Cardea is a deity protecting households from harmful spirits entering through doors.
Symbolism: the hinge (the mechanism that allows a door or gate to open and close), in Latin _cardo, cardinis_.
