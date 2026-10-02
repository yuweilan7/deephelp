import hashlib
import importlib.util
import unittest
from pathlib import Path

script = Path(__file__).resolve().parents[1] / "scripts/update-redis-acl.py"
spec = importlib.util.spec_from_file_location("learning_acl_policy", script)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class LearningACLTests(unittest.TestCase):
    def setUp(self):
        self.password_hash = "#" + hashlib.sha256(b"synthetic-password").hexdigest()
        self.others = b"user default off\nuser deephelp_admin on #" + b"a" * 64 + b" ~* &* +@all\n"
        self.original = (
            self.others
            + ("user deephelp_app on " + self.password_hash + " ~deephelp:* +get +set\n").encode()
        )

    def test_opens_every_permission_and_preserves_all_credentials(self):
        result = policy.extend_acl(self.original)
        expected = (
            self.others + ("user deephelp_app on " + self.password_hash + " ~* &* +@all\n").encode()
        )
        self.assertEqual(result, expected)

    def test_reapplication_does_not_accumulate_or_change_rules(self):
        result = policy.extend_acl(self.original)
        self.assertEqual(policy.extend_acl(result), result)

    def test_clears_explicit_denials_and_old_selectors(self):
        source = self.original.rstrip(b"\n") + b" -eval -@admin (+get ~old:*)\n"
        result = policy.extend_acl(source)
        self.assertNotIn(b"-eval", result)
        self.assertNotIn(b"old:", result)
        self.assertIn(self.password_hash.encode(), result)

    def test_missing_or_duplicate_app_cannot_rewrite_configuration(self):
        for source in [self.others, self.original + self.original]:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    policy.extend_acl(source)

    def test_passwordless_or_unrecognized_password_format_is_rejected(self):
        for credentials in ["nopass", ">synthetic-password", "#short"]:
            source = self.others + ("user deephelp_app on " + credentials + " ~* +@all\n").encode()
            with self.subTest(credentials=credentials):
                with self.assertRaises(ValueError):
                    policy.extend_acl(source)


if __name__ == "__main__":
    unittest.main()
