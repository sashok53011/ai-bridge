# AI Bridge — Simple Guide

AI Bridge is a small helper program that runs on your computer. It gives
your apps **one single address** to talk to many different AI providers
(OpenAI, Groq, Ollama, and others). You add your API keys once, and AI
Bridge picks the right one and forwards the request.

You do not need to be a programmer to use it. The whole thing is controlled
through a simple web page in your browser.

## What you need

- A computer with Windows, macOS, or Linux.
- An internet connection (only for downloading and for cloud providers).
- An API key from at least one AI provider (OpenAI, Groq, …). Local
  models (Ollama) need no key.

## Quick start (3 steps)

### 1. Install

Download the project and open a terminal in its folder:

```
uv sync
```

This installs everything needed. No compiler, no build step.

### 2. Log in once

Create a free account on the catalogue server, then put your login and
password into the program. The easiest way is to set two environment
variables (PowerShell example):

```
$env:AI_BRIDGE_EMAIL = "you@example.com"
$env:AI_BRIDGE_PASSWORD = "your-password"
```

Or copy `config.example.toml` to `config.toml` and fill in the `email`
and `password` lines there.

### 3. Start the service

```
uv run python app.py --serve
```

A web page opens at `http://127.0.0.1:8080/`. That is your control panel.

## Connect your app

Your apps (ChatGPT clients, IDE plugins, scripts) talk to AI Bridge
instead of directly to a provider. Give them:

- **Address:** `http://127.0.0.1:8080/v1`
- **Token:** run `uv run python app.py --token` and copy the value.

The token is like a password for your local service — keep it private.

Then just use the app as usual. AI Bridge forwards every request to the
best available provider and falls back automatically if one fails.

## The control panel (web page)

Open `http://127.0.0.1:8080/` in your browser and log in. You will see:

- **Catalogue** — all providers. Switch them on/off, add your own
  (Ollama on another PC, LM Studio, etc.).
- **Keys** — enter an API key for a provider. It stays on your device;
  the page only shows "set / not set".
- **Check** — test a provider and see if it answers.
- **Statistics** — how many requests you made, tokens used, which model
  answered.
- **Journal** (admin only) — a log of every request, with the request
  and answer bodies (collapsed by default, click to expand).

## Safety

- Your API keys are stored **encrypted on your device**. They are never
  sent to the catalogue server.
- The local service asks for a token on every request. On your own
  computer this is automatic; over the internet you must use HTTPS or a
  VPN (Tailscale is recommended).
- Do not share your token. Treat it like a password.

## Need help?

- Read `CONTEXT.md` for the full picture.
- Run `uv run pytest` to check that everything works (all tests are
  offline).
- Open an issue on GitHub if something is broken.
