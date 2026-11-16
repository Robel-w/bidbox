# Local demo configuration

Set `LLM_BASE_URL` to your OpenAI-compatible endpoint and `LLM_MODEL` to an exact model ID returned by that endpoint's model list. The demo and evaluation runners check availability before starting an investigation and fail with the available IDs if the configured model is missing. For Ollama, install the chosen model with `ollama pull <model>` or choose an already installed model. A model being available does not guarantee a valid evidence review; invalid reviewer JSON is recorded as a failed review requiring manual verification.

## Windows / NO_PROXY

In the current environment, the OpenAI client's HTTP transport rejects a bracketed IPv6 entry `[::1]` in `NO_PROXY` with `InvalidURL: Invalid port: ':1]'`. The bare entry `::1` was accepted in a local parsing probe; remove the bracketed entry rather than disabling all proxy handling.

For a local demo in Git Bash, an override scoped to one process is:

```bash
NO_PROXY=localhost,127.0.0.1 LLM_MODEL=llama3.2:latest .venv/Scripts/python scripts/run_demo.py --ocid ocds-synth-kenya-000100
```

This command uses synthetic fixture data and prompts for a human approver. Use the model installed on your machine, not an assumed model. Do not copy credentials into commands or tracked files.

## Docker

Neither `Dockerfile` nor `docker-compose.yml` sets `NO_PROXY`. Compose loads the local `.env` file: do not put `[::1]` in that file's `NO_PROXY`, and check any proxy variables supplied by the container runtime. Configure `LLM_BASE_URL` to an endpoint reachable from inside the container; container localhost is not the host's model server. Docker deployment was not exercised for this performance change.
