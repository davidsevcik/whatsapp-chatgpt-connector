"""Import common iOS/Android text exports with explicit date ordering."""
from dataclasses import dataclass
from datetime import datetime
import re
from zoneinfo import ZoneInfo


@dataclass
class Message:
    timestamp: str
    sender: str | None
    text: str


DATE = r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"
TIME = r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?"
HEADER = re.compile(
    rf"^(?:\[(?P<di>{DATE}),?\s+(?P<ti>{TIME})\]\s*|"
    rf"(?P<da>{DATE}),?\s+(?P<ta>{TIME})\s+-\s+)(?P<body>.*)$"
)


def parse_export(text: str, date_order: str = "dmy", timezone: str = "Europe/Prague") -> list[Message]:
    if date_order not in {"dmy", "mdy", "ymd"}:
        raise ValueError("date_order must be dmy, mdy or ymd")
    zone = ZoneInfo(timezone)
    messages: list[Message] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.lstrip("\ufeff\u200e\u200f").replace("\u202f", " ").replace("\u00a0", " ")
        match = HEADER.match(line)
        if not match:
            if messages:
                messages[-1].text += "\n" + raw
            elif line.strip():
                raise ValueError(f"Unrecognized export header on line {number}; expected an iOS/Android text export")
            continue
        date = match["di"] or match["da"]
        time = (match["ti"] or match["ta"]).upper()
        parts = re.split(r"[./-]", date)
        fields = dict(zip(date_order, map(int, parts)))
        if fields["y"] < 100:
            fields["y"] += 2000
        try:
            clock = None
            for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p"):
                try:
                    clock = datetime.strptime(time, fmt)
                    break
                except ValueError:
                    pass
            if clock is None:
                raise ValueError("invalid time")
            dt = datetime(fields["y"], fields["m"], fields["d"], clock.hour, clock.minute, clock.second, tzinfo=zone)
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp on line {number}; check --date-order") from exc
        body = match["body"]
        sender, sep, content = body.partition(": ")
        messages.append(Message(dt.isoformat(), sender if sep else None, content if sep else body))
    if not messages:
        raise ValueError("No messages found")
    return messages
