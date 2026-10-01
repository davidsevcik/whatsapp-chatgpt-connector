import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analyzer import VERSION, offline, openai_analysis
from .parser import parse_export
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import WhatsApp exports and surface conversations needing attention.")
    parser.add_argument("--db", default="data/attention.db", help="SQLite path (default data/attention.db)")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("import", help="Import a UTF-8 .txt export")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--chat", required=True, help="Stable chat name; reuse it for subsequent exports")
    ingest.add_argument("--date-order", choices=["dmy", "mdy", "ymd"], default="dmy")
    ingest.add_argument("--timezone", default="Europe/Prague")
    analyze = commands.add_parser("analyze", help="Analyze each chat's most recent messages")
    analyze.add_argument("--me", required=True, help="Your exact sender name in exports")
    analyze.add_argument("--provider", choices=["offline", "openai"], default="offline")
    analyze.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    analyze.add_argument("--context", type=int, default=100, help="Recent messages per chat (1-500)")
    analyze.add_argument("--chat", help="Only analyze this chat")
    analyze.add_argument("--force", action="store_true", help="Reanalyze unchanged chats (may incur API costs)")
    digest = commands.add_parser("digest", help="Print undismissed attention items")
    digest.add_argument("--json", action="store_true")
    dismiss = commands.add_parser("dismiss", help="Hide a chat until its next changed import/forced analysis")
    dismiss.add_argument("chat")
    search = commands.add_parser("search", help="Search local messages; returns up to 50 matches")
    search.add_argument("query")
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "analyze":
            if not 1 <= args.context <= 500:
                raise ValueError("--context must be between 1 and 500")
            if args.provider == "openai" and (not args.model or not os.environ.get("OPENAI_API_KEY")):
                raise ValueError("OpenAI mode requires --model (or OPENAI_MODEL) and OPENAI_API_KEY")
        store = Store(args.db)
        if args.command == "import":
            if not args.chat.strip():
                raise ValueError("--chat must not be blank")
            messages = parse_export(args.file.read_text(encoding="utf-8-sig"), args.date_order, args.timezone)
            inserted = store.ingest(args.chat, messages)
            print(f"Imported {inserted} new messages; {len(messages) - inserted} already stored.")
        elif args.command == "analyze":
            chats = store.chats()
            if args.chat and args.chat not in chats:
                raise ValueError("Unknown chat; import an export first")
            chats = [args.chat] if args.chat else chats
            analyzed = skipped = 0
            now = datetime.now(ZoneInfo("Europe/Prague")).isoformat()
            for chat in chats:
                messages = store.messages(chat, args.context)
                config = [VERSION, args.me, args.provider, args.model if args.provider == "openai" else None, args.context, messages]
                snapshot = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if not args.force and store.cached(chat, snapshot):
                    skipped += 1
                    continue
                result = offline(messages, args.me) if args.provider == "offline" else openai_analysis(messages, args.me, args.model, now)
                result["provider"] = args.provider
                result["model"] = args.model if args.provider == "openai" else None
                result["context_messages"] = len(messages)
                store.save(chat, snapshot, result, now)
                analyzed += 1
            print(f"Analyzed {analyzed} chats; skipped {skipped} unchanged chats.")
        elif args.command == "digest":
            items = store.digest()
            if args.json:
                print(json.dumps(items, ensure_ascii=False, indent=2))
            elif not items:
                print("No analyzed, undismissed conversations need attention. Import and analyze first if needed.")
            else:
                for item in items:
                    print(f"[{item['urgency'].upper()}] {item['chat']} ({item['provider']})")
                    print(f"  {item['reason']}")
                    print(f"  Action: {item['requested_action']}")
                    if item["deadline"]:
                        print(f"  Deadline: {item['deadline']}")
                    if item["suggested_reply"]:
                        print(f"  Draft reply: {item['suggested_reply']}")
                    print(f"  Evidence message IDs: {item['evidence_ids']}")
        elif args.command == "dismiss":
            if not store.dismiss(args.chat):
                raise ValueError("No analysis found for this chat")
            print(f"Dismissed {args.chat}.")
        elif args.command == "search":
            print(json.dumps(store.search(args.query), ensure_ascii=False, indent=2))
    except (ValueError, OSError, ZoneInfoNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if store:
            store.close()
