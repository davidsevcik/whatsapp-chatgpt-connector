import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wa_attention.analyzer import offline, openai_analysis, validate
from wa_attention.cli import main
from wa_attention.parser import parse_export
from wa_attention.store import Store


class ParserTests(unittest.TestCase):
    def test_android_multiline_and_system_message(self):
        messages = parse_export("1.10.2026, 8:00 - Encryption enabled\n1.10.2026, 8:01 - Petra: Prosím?\nDruhý řádek")
        self.assertIsNone(messages[0].sender)
        self.assertEqual(messages[1].text, "Prosím?\nDruhý řádek")
        self.assertEqual(messages[1].timestamp, "2026-10-01T08:01:00+02:00")

    def test_ios_unicode_12_hour_mdy(self):
        message = parse_export("\u200e[10/1/26, 9:05:02\u202fPM] Petra: Hi", "mdy")[0]
        self.assertEqual(message.timestamp, "2026-10-01T21:05:02+02:00")

    def test_ymd_and_bom(self):
        message = parse_export("\ufeff[2026-10-01, 08:00] David: Hi", "ymd")[0]
        self.assertEqual(message.sender, "David")

    def test_bad_date_and_unknown_format_fail(self):
        for text in ("31/31/2026, 08:00 - Petra: hi", "not an export", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_export(text)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_repeat_import_preserves_same_minute_duplicate_messages(self):
        messages = parse_export("1/10/26, 08:00 - Petra: Hi\n1/10/26, 08:00 - Petra: Hi")
        self.assertEqual(self.store.ingest("A", messages), 2)
        self.assertEqual(self.store.ingest("A", messages), 0)
        self.assertEqual(len(self.store.messages("A", 100)), 2)

    def test_chats_do_not_deduplicate_each_other(self):
        messages = parse_export("1/10/26, 08:00 - Petra: Hi")
        self.assertEqual(self.store.ingest("A", messages), 1)
        self.assertEqual(self.store.ingest("B", messages), 1)

    def test_dismiss_cache_and_new_message_invalidation(self):
        self.store.ingest("A", parse_export("1/10/26, 08:00 - Petra: Please help?"))
        result = offline(self.store.messages("A", 100), "David")
        self.store.save("A", "v1", result, "now")
        self.assertTrue(self.store.cached("A", "v1"))
        self.assertEqual(len(self.store.digest()), 1)
        self.store.dismiss("A")
        self.assertEqual(self.store.digest(), [])
        self.store.ingest("A", parse_export("1/10/26, 08:01 - Petra: Urgent!"))
        self.assertFalse(self.store.cached("A", "v1"))


class AnalysisTests(unittest.TestCase):
    def messages(self, text):
        return [{"id": i + 1, "timestamp": message.timestamp, "sender": message.sender, "text": message.text}
                for i, message in enumerate(parse_export(text))]

    def test_czech_request_and_urgency(self):
        result = offline(self.messages("1/10/26, 08:00 - Petra: Prosím, můžeš to dnes zkontrolovat?"), "David")
        self.assertTrue(result["needs_attention"])
        self.assertEqual(result["urgency"], "high")
        self.assertIsNone(result["deadline"])

    def test_no_attention_after_self_reply_or_system_question(self):
        for text in ("1/10/26, 08:00 - Petra: Please help?\n1/10/26, 08:01 - David: Done",
                     "1/10/26, 08:00 - Encryption enabled?",
                     "1/10/26, 08:00 - Petra: Nice photo"):
            self.assertFalse(offline(self.messages(text), "David")["needs_attention"])

    def test_hallucinated_evidence_rejected(self):
        messages = self.messages("1/10/26, 08:00 - Petra: Please help?")
        result = offline(messages, "David")
        result["evidence_ids"] = [999]
        with self.assertRaises(ValueError):
            validate(result, messages, "David")

    def test_api_adapter_request_and_response(self):
        messages = self.messages("1/10/26, 08:00 - Petra: Please help?")
        result = offline(messages, "David")
        payload = {"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("wa_attention.analyzer.urlopen") as call:
            call.return_value.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
            self.assertEqual(openai_analysis(messages, "David", "test-model", "now"), result)
            body = json.loads(call.call_args.args[0].data)
            self.assertFalse(body["store"])
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertEqual(body["model"], "test-model")

    def test_incomplete_and_refusal_are_not_accepted(self):
        for payload in ({"status": "incomplete"}, {"status": "completed", "output": [{"content": [{"type": "refusal"}]}]}):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), patch("wa_attention.analyzer.urlopen") as call:
                call.return_value.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
                with self.assertRaises(ValueError):
                    openai_analysis([], "David", "test-model", "now")


class CLITests(unittest.TestCase):
    def test_import_analyze_digest_reimport_dismiss(self):
        with tempfile.TemporaryDirectory() as directory:
            export = Path(directory) / "export.txt"
            export.write_text("1/10/26, 08:00 - Petra: Please help today?", encoding="utf-8")
            base = ["--db", str(Path(directory) / "test.db")]
            def run(args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    main(base + args)
                return output.getvalue()
            self.assertIn("1 new", run(["import", str(export), "--chat", "A"]))
            self.assertIn("Analyzed 1", run(["analyze", "--me", "David"]))
            self.assertEqual(json.loads(run(["digest", "--json"]))[0]["chat"], "A")
            self.assertIn("0 new", run(["import", str(export), "--chat", "A"]))
            self.assertIn("skipped 1", run(["analyze", "--me", "David"]))
            run(["dismiss", "A"])
            self.assertEqual(json.loads(run(["digest", "--json"])), [])


if __name__ == "__main__":
    unittest.main()
