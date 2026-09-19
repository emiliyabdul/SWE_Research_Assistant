# Artefacts

Output of one full demo run over all 5 sample questions from
`data/research_questions.json`, generated offline (no API keys, no network):

- `qN.txt` — human-readable CLI output: `python -m researcher ask --offline --no-cache "<question>"`
- `qN.json` — machine-readable output: same command with `--json`
- `benchmark.txt` — sequential-vs-parallel table: `python scripts/bench.py --offline --runs 3`

Regenerate any of these with the commands above from the repository root.
Live-mode artefacts (real LLM + real sources) can be produced by dropping
`--offline` once API keys are configured in `.env`.
