# WhatsApp Attention

A runnable Python + SQLite prototype that imports selected WhatsApp text exports,
identifies conversations that may need your attention, and prints an attention digest.
It has no runtime dependencies. It does not connect to WhatsApp live or send messages.

## Quick demo

Python 3.11+ is required. Run from the repository root:

```bash
python -m wa_attention import examples/demo.txt --chat Demo
python -m wa_attention analyze --me David
python -m wa_attention digest
```

The synthetic Czech conversation produces one high urgency item. The default offline
mode uses basic Czech/English question and request rules. It is a pipeline demonstration,
not reliable semantic triage. It leaves deadlines and suggested replies empty.

Optionally install the command into a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
wa-attention --help
```

On Windows, activate with `.venv\Scripts\activate`. Windows may also need
`python -m pip install tzdata` if IANA timezone data is unavailable.

## Import your selected chats

In WhatsApp, open a chat's options and choose **Export chat**, without media. Extract
the `.txt` file if the export is packaged in a ZIP. Keep personal exports in the ignored
`exports/` directory. This prototype accepts UTF-8 text, not ZIPs or attachments.

```bash
python -m wa_attention import exports/project.txt --chat Project --date-order dmy --timezone Europe/Prague
python -m wa_attention analyze --me 'David Sevcik'
python -m wa_attention digest --json
python -m wa_attention search 'návrh'
python -m wa_attention dismiss Project
```

`--me` must exactly match your sender name in the export. Use the same `--chat` name
each time you export the same conversation; names do not come from the file.
Use `mdy` for month/day/year exports, or `ymd` for year/month/day. The default is `dmy`;
ambiguous dates are interpreted according to that setting. Two digit years map to 2000–2099.
Common Android `date, time - sender: text` and iOS `[date, time] sender: text` formats
are supported, including multiline messages, 12 hour times and Unicode spacing.
Other localized formats may require extending `parser.py`. Timestamp-like continuation
lines and sender-like system notices can be ambiguous in WhatsApp's plain-text format.
Timezone timestamps use Python's default fold for ambiguous DST times.

## Optional OpenAI analysis

Only `--provider openai` makes network requests. It sends the selected recent chat text,
sender names, timestamps and your configured name to OpenAI. Use it with conversations
you are comfortable processing there. Set your own API key and a model available to
your account that supports the Responses API with strict JSON Schema outputs:

```bash
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='your-compatible-model-id'
python -m wa_attention analyze --me 'David Sevcik' --provider openai --chat Project
python -m wa_attention digest
```

`.env.example` documents the variables; `.env` is not automatically loaded.
API usage is billed separately from ChatGPT. No model is hardcoded and no key is included.
The adapter uses the [Responses API](https://developers.openai.com/api/docs/guides/text)
with [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
`store: false`, a 60 second timeout and no tools. It validates field types and incoming
evidence IDs locally, and rejects refusals and incomplete responses. Failed requests
are not saved. Rerunning analyzes the remaining uncached chats; completed chats are skipped.
No real API call was made during prototype verification; the adapter has mocked contract tests.

Each result contains `needs_attention`, `urgency`, `reason`, `deadline`,
`requested_action`, `suggested_reply`, and `evidence_ids`. Suggested replies are drafts
for manual review. Review the source conversation before acting on a model's assessment.
Chat content is treated as untrusted data in the system prompt, and the model has no
ability to send messages or execute actions.

## Data and incremental behavior

The default database is `data/attention.db`. Override it before the subcommand:

```bash
python -m wa_attention --db /path/to/attention.db digest
```

Imports are transactional. Messages are deduplicated per chat using timestamp, sender,
text and occurrence count, preserving repeated identical messages within one export.
For accurate overlap matching, reexport the complete chat rather than arbitrary slices;
sliced exports containing identical same-time messages can have ambiguous occurrence counts.
New messages or backfilled messages invalidate the chat's stored assessment. Reimporting
an identical export preserves it. A changed provider, model, name or context triggers
reanalysis. Dismissals persist until a changed import or explicit forced analysis.

Analysis uses the last 100 messages per chat; configure `--context` from 1 to 500.
Older unresolved obligations may be missed. The offline rules only inspect messages
after your latest reply and can miss obligations even when your reply did not resolve them.
The current prototype emits one overall action per chat, so multiple obligations may be
combined or missed. Unchanged chats are cached indefinitely; use `--force` to refresh
time-sensitive assessments, including approaching deadlines:

```bash
python -m wa_attention analyze --me David --provider openai --chat Project --force
```

The database stores personal content in plain text. Use a protected local disk. Database
files, exports, credentials and generated digest JSON are ignored by Git. The included
demo is fictional. No telemetry, server, scheduler, live account bridge or auto-replies
are included.

## Development

```bash
python -m unittest discover -s tests -v
```

Tests cover export formats, date ordering, duplicate imports, preserved identical
messages, chat isolation, cache invalidation, dismissals, Czech triage, evidence
validation, mocked OpenAI responses/refusals and the CLI workflow. GitHub Actions
runs these on Python 3.11, 3.12 and 3.13.

Modules separate ingestion (`parser.py`), persistence (`store.py`), classification
(`analyzer.py`) and user commands (`cli.py`). A future live ingestion adapter can write
to `Store.ingest()` without changing the triage pipeline. That adapter would need stable
native message IDs, reconnect handling and an explicit account-linking flow. The prototype
deliberately starts with exports so the attention workflow can be evaluated first.
