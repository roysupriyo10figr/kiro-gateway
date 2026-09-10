# Kiro Gateway

Use Claude Code with your organization's Kiro account through a local gateway.
This fork includes compatibility for Claude Code requests containing embedded
system messages, for streaming, non-streaming, and token counting.

These instructions target macOS and AWS IAM Identity Center (SSO). Start with
everything on one Mac; remote access over Tailscale is optional.

Based on [Jwadow's Kiro Gateway](https://github.com/jwadow/kiro-gateway).
Licensed under [AGPL-3.0](LICENSE).

## 1. Install the tools

With [Homebrew](https://brew.sh/) installed:

```sh
brew install uv
brew install --cask claude-code
curl -fsSL https://cli.kiro.dev/install | bash
```

Open a new terminal, then verify:

```sh
uv --version
kiro-cli --version
claude --version
```

Installation references: [Kiro CLI](https://kiro.dev/downloads/) and
[Claude Code](https://code.claude.com/docs/en/installation).

## 2. Sign in to Kiro with your organization

Run this on the Mac that will host the gateway:

```sh
kiro-cli login
kiro-cli whoami
```

Choose IAM Identity Center and enter your organization's SSO start URL and
region. Your administrator supplies these values; they are not the same for
every organization.

For an explicit login, including when connected over SSH:

```sh
kiro-cli login --license pro \
  --identity-provider "https://YOUR-ORGANIZATION.awsapps.com/start" \
  --region "YOUR-SSO-REGION" \
  --use-device-flow
```

Complete the browser login using the URL and code displayed by the CLI.

Check the macOS credential database:

```sh
test -f "$HOME/Library/Application Support/kiro-cli/data.sqlite3" \
  && echo "Kiro credentials found"
```

This is the path used by this setup. If the check prints nothing, finish the CLI
login and confirm where your CLI version stores its database before proceeding.
The gateway reads the database and refreshes tokens automatically. Run it as the
same macOS user who signed in; no token extraction is needed.

## 3. Clone this fork

```sh
mkdir -p "$HOME/developer/misc"
git clone https://github.com/roysupriyo10figr/kiro-gateway.git \
  "$HOME/developer/misc/kiro-gateway"
cd "$HOME/developer/misc/kiro-gateway"
```

## 4. Create your single configuration file

Run this once from the repository directory. It creates the actual `.env`,
with a random gateway key and your expanded `$HOME` path. There is no example
file to copy. The command refuses to overwrite an existing `.env`; edit that
file directly if you already have one.

```sh
(
  umask 077
  set -C
  kiro_gateway_key="$(openssl rand -hex 32)" || exit 1
  cat > .env <<EOF
# Gateway password, shared only with clients connecting to this server.
PROXY_API_KEY="$kiro_gateway_key"

# macOS Kiro CLI SSO credentials. Keep this database on the gateway Mac.
KIRO_CLI_DB_FILE="$HOME/Library/Application Support/kiro-cli/data.sqlite3"
SQLITE_READONLY=false
ACCOUNT_SYSTEM=false

# Local-only by default.
SERVER_HOST="127.0.0.1"
SERVER_PORT="8000"
DEBUG_MODE="off"

# Read by the shell function below.
KIRO_GATEWAY_URL="http://127.0.0.1:8000"
KIRO_CLAUDE_MODEL="claude-opus-5"
KIRO_CLAUDE_EFFORT="max"
EOF
)
```

`.env` is ignored by Git. Keep it private; do not commit it or your credential
database. The gateway key is a password you generate, not an AWS or Anthropic
API key. The rest of the gateway settings use their built-in defaults.

## 5. Start the gateway

Standalone server startup checks SSO automatically when `KIRO_CLI_DB_FILE` is
configured. It skips login when `kiro-cli whoami`
succeeds, otherwise waits for login to complete and checks again before starting.
Set `KIRO_SSO_START_URL` and `KIRO_SSO_REGION` in the launching environment to
provide your organization's details automatically. Gateway flags are forwarded:

```sh
KIRO_SSO_START_URL="https://YOUR-ORGANIZATION.awsapps.com/start" \
KIRO_SSO_REGION="us-east-1" \
  uv run --with-requirements requirements.txt python main.py --host 127.0.0.1 --port 8000
```

The CLI login check reports local sign-in state, not a live authorization test.
The gateway handles normal token refresh; if a saved session has been revoked,
run `kiro-cli login` again to renew it.

```sh
cd "$HOME/developer/misc/kiro-gateway"
uv run --with-requirements requirements.txt python main.py
```

Leave this terminal running. Stop with Ctrl-C. Restart with the same command
after changing `.env` or updating the code.

In another terminal:

```sh
curl --fail http://127.0.0.1:8000/health
```

For a different port, update both `SERVER_PORT` and `KIRO_GATEWAY_URL`.
CLI flags such as `--host` and `--port` override the file settings.

## 6. Add kiro-claude to your shell

Paste this function into `~/.zshrc` (macOS default), or `~/.bashrc` if you use
Bash. Adjust the configuration path if you cloned elsewhere.

```sh
kiro-claude() (
  kiro_env_file="$HOME/developer/misc/kiro-gateway/.env"
  if [ ! -r "$kiro_env_file" ]; then
    printf 'Cannot read gateway configuration: %s\n' "$kiro_env_file" >&2
    return 1
  fi

  . "$kiro_env_file" || return 1
  if [ -z "${PROXY_API_KEY:-}" ] || [ -z "${KIRO_GATEWAY_URL:-}" ]; then
    printf '%s\n' 'Set PROXY_API_KEY and KIRO_GATEWAY_URL in your .env.' >&2
    return 1
  fi

  unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY
  unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN

  export ANTHROPIC_BASE_URL="$KIRO_GATEWAY_URL"
  export ANTHROPIC_API_KEY="$PROXY_API_KEY"

  command claude --model "${KIRO_CLAUDE_MODEL:-claude-opus-5}" \
    --effort "${KIRO_CLAUDE_EFFORT:-max}" "$@"
)
```

Reload your shell configuration:

```sh
source ~/.zshrc
```

Then run from any project directory:

```sh
kiro-claude
kiro-claude --resume
kiro-claude -p "Reply with OK."
```

The function forwards every argument and scopes its environment changes to the
Claude Code process. Your regular `claude` command keeps its existing settings.
If an older executable or alias has the same name, remove that alias before
defining the function; use `type kiro-claude` to confirm the function is active.

The default is Opus 5 with maximum effort. Model access depends on the gateway
account. Change `KIRO_CLAUDE_MODEL` and `KIRO_CLAUDE_EFFORT` in `.env` as needed.
Passing an effort setting does not guarantee identical reasoning behavior to
Anthropic's hosted service.

## Optional: use Sol for one session

Keep the wrapper's Opus default and override it for one launch:

```sh
CLAUDE_CODE_MAX_CONTEXT_TOKENS=272000 \
  kiro-claude --model gpt-5.6-sol --effort max
```

Sol's advertised context window is 272K, not 1M. The gateway maps Claude Code's
adaptive thinking and effort controls to Sol's native `reasoning.effort` field.
The verified levels are `none`, `low`, `medium`, `high`, `xhigh`, and `max`.
OpenAI `reasoning_effort` uses the same model-specific mapping. Sol does not
accept Claude's fixed `budget_tokens`; use adaptive thinking and an effort level.

Sol can send reasoning after its answer. For Claude Code compatibility, the
Anthropic streaming adapter buffers Sol's answer and tool events until reasoning
has arrived, then emits them after the thinking blocks. This prevents a trailing
thinking block from replacing the visible final answer in Claude Code. Keepalives
continue during the wait. OpenAI streaming preserves its existing event order;
non-streaming Anthropic responses already place thinking before answer text.

## Optional: connect over a Tailnet

Only the gateway Mac needs Kiro CLI and the SSO database. A remote client needs
Claude Code, connectivity to the gateway, and the gateway password. Requests
use the account signed in on the gateway Mac.

1. Connect both devices to your Tailnet.
2. On the gateway Mac, find its Tailscale IPv4 address with `tailscale ip -4`
   or in the Tailscale app.
3. Change `SERVER_HOST` in the gateway's `.env` to that address and restart.
4. On each client, set `KIRO_GATEWAY_URL` to the gateway's reachable address.

For example, a gateway named `thalia` could use:

```dotenv
SERVER_HOST="100.116.20.25"
SERVER_PORT="8000"
KIRO_GATEWAY_URL="http://thalia:8000"
```

Replace these example values with your own. `thalia` relies on Tailscale
MagicDNS/name resolution, not mDNS; use its Tailscale IP if the name does not
resolve. Binding only to the Tailscale IP makes the localhost health-check URL
unavailable, so check the Tailnet URL instead:

```sh
curl --fail http://thalia:8000/health
```

On a client that does not host the gateway, create
`$HOME/developer/misc/kiro-gateway/.env` with just these values, then use the same
shell function from step 6:

```dotenv
PROXY_API_KEY="THE-SAME-KEY-AS-THE-GATEWAY"
KIRO_GATEWAY_URL="http://thalia:8000"
KIRO_CLAUDE_MODEL="claude-opus-5"
KIRO_CLAUDE_EFFORT="max"
```

Create the parent directory first and run `chmod 600` on this file. Transfer
only the gateway password, not the SSO database. Tailnet access rules must allow
the client to reach port 8000. Tailscale encrypts traffic between the devices;
the gateway still requires its password.

## Troubleshooting

### Prompt caching and credit usage

The gateway translates explicit `cache_control: {"type": "ephemeral"}` markers
into Kiro `cachePoint: {"type": "default"}` objects for messages and tools. OpenAI-compatible clients
can supply the same extension on messages or tools. Both APIs and response modes
share the translation. Standard OpenAI requests without markers retain Kiro's
automatic behavior.

The shared planner preserves source text units independently of where markers
appear. Eligible content blocks become separate native message units, so moving
a marker does not reframe the earlier prefix. System units remain ordered, and
generated suffixes stay outside earlier marked prefixes. Tool-result batches
and reasoning groups must remain atomic: a marker inside an indivisible group
is rejected with an actionable error rather than silently moved. Checkpoints
are not arbitrarily limited to four. Requested TTLs are not guaranteed because
Kiro controls cache lifetime. Set `KIRO_PROMPT_CACHE=false`
to disable gateway-added checkpoints; this does not disable Kiro's own caching.

Client beta headers are not blindly forwarded to Kiro's different API. Missing
cache token counters mean **unknown**, not proof of a cache miss. Do not infer
credit savings from estimated token counts alone.

Normal logs include upstream request IDs, HTTP attempt counts, header latency,
first-byte wait time, and metered credit usage. `LOG_LEVEL=DEBUG` also includes
checkpoint fingerprints without prompt text or credentials. Compare actual
credits for repeated prefixes and check retries before diagnosing an efficiency
regression. See [the cache verification notes](docs/cache-verification.md).

For isolated verification, `KIRO_CACHE_DIAGNOSTICS=true` also exposes compiled
prefix fingerprints in response headers. These prove translation stability,
not backend cache hits. Signed-reasoning round trips and all-provider cache
behavior are not yet comprehensively verified.

### Reasoning and streaming

The gateway forwards Anthropic adaptive thinking and `output_config.effort`
through Kiro's native `additionalModelRequestFields`. OpenAI
`reasoning_effort` values other than `none` are forwarded as native effort
settings too. Kiro validates model support and allowed values. Native requests
do not receive the legacy fake-thinking prompt instructions.

Native reasoning text and signatures are returned as Anthropic thinking blocks
and signature deltas, or OpenAI `reasoning_content` and `reasoning_signature`.
This works in streaming and non-streaming modes. Legacy requests without native
controls retain the existing configurable fake-reasoning behavior.

Streaming responses send keepalives every 10 seconds during idle periods
(Anthropic ping events, OpenAI SSE comments). These keep the connection active;
they are not model output and do not make inference faster. Initial upstream
connection/authentication and response-header waits still occur before the SSE
response starts, preserving HTTP error status codes. Non-streaming responses
wait for completion and do not send keepalives.

- **Connection refused:** start the gateway and check that its bind address and
  port match `KIRO_GATEWAY_URL`. For remote access, keep the gateway Mac awake.
- **401:** the client and gateway `PROXY_API_KEY` values must match.
- **SSO expired or credential database missing:** run `kiro-cli login` on the
  gateway Mac, check `kiro-cli whoami`, then restart the gateway.
- **Model unavailable:** choose a model available to the gateway account.
  The authenticated `GET /v1/models` endpoint lists advertised models.
- **422 mentioning a system role:** confirm you started this fork from the
  updated checkout and restarted the running gateway.
- **Requests still use another provider:** run `type kiro-claude` to verify
  the function is active, then check your Claude Code managed settings.

## Development

```sh
uv run --with-requirements requirements.txt pytest -q
```

See [tests/README.md](tests/README.md) for testing guidance. For advanced API,
Docker, and multi-account configuration, see the
[upstream documentation](https://github.com/jwadow/kiro-gateway#readme).
