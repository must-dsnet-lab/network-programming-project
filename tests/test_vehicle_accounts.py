import io
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from rover.vehicle.accounts import AccountStore, main


class TestVehicleAccounts(unittest.TestCase):
    password = "fixture password only 123"

    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "storage" / "accounts.sqlite3"
        self.store = AccountStore(self.path)

    def test_registration_persists_observer_with_salted_password_digest(self) -> None:
        self.assertIsNone(self.store.register("alice", self.password))
        self.assertIsNone(self.store.register("bob", self.password))
        reloaded = AccountStore(self.path)
        self.assertEqual(reloaded.authenticate("alice", self.password), "observer")
        self.assertIsNone(reloaded.authenticate("alice", self.password + "x"))
        self.assertIsNone(reloaded.authenticate("unknown", self.password))
        self.assertNotIn(self.password.encode(), self.path.read_bytes())

        database = sqlite3.connect(self.path)
        self.addCleanup(database.close)
        rows = database.execute("SELECT salt, password_hash FROM accounts ORDER BY client_id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertNotEqual(rows[0][1], rows[1][1])

    def test_rejects_invalid_fields_without_creating_accounts(self) -> None:
        for client_id in ("", " Alice", "a/b", "a" * 65, "a\ud800", None):
            with self.subTest(client_id=client_id):
                self.assertEqual(self.store.register(client_id, self.password), "invalid_account")
                self.assertIsNone(self.store.role(client_id))
        for password in ("short", " " * 12, "valid length \ud800", "🚙" * 257, None):
            with self.subTest(password=password):
                self.assertEqual(self.store.register("alice", password), "invalid_credential")
                self.assertIsNone(self.store.authenticate("alice", password))
        self.assertEqual(self.store.list_accounts(), [])

    def test_valid_utf8_password_is_preserved_including_spaces(self) -> None:
        password = " 车端🚙 搭配长口令 2026 "
        self.assertIsNone(self.store.register("Desk_A.2-test", password))
        self.assertEqual(self.store.authenticate("Desk_A.2-test", password), "observer")
        self.assertIsNone(self.store.authenticate("Desk_A.2-test", password.strip()))

    def test_competing_registrations_claim_account_once(self) -> None:
        passwords = (self.password, self.password + "x")
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda password: self.store.register("alice", password), passwords))
        self.assertEqual(attempts.count(None), 1)
        self.assertEqual(attempts.count("account_exists"), 1)
        self.assertEqual(self.store.authenticate("alice", passwords[attempts.index(None)]), "observer")
        self.assertIsNone(self.store.authenticate("alice", passwords[attempts.index("account_exists")]))

    def test_role_can_be_changed_without_password_and_survives_restart(self) -> None:
        self.assertIsNone(self.store.role("alice"))
        self.assertFalse(self.store.set_role("alice", "operator"))
        self.assertIsNone(self.store.register("alice", self.password))
        self.assertTrue(self.store.set_role("alice", "operator"))
        self.assertFalse(self.store.set_role("alice", "administrator"))
        reloaded = AccountStore(self.path)
        self.assertEqual(reloaded.role("alice"), "operator")
        self.assertEqual(reloaded.authenticate("alice", self.password), "operator")
        self.assertTrue(reloaded.set_role("alice", "observer"))
        self.assertEqual(self.store.role("alice"), "observer")

    def test_admin_cli_reports_roles_without_printing_credentials(self) -> None:
        self.assertIsNone(self.store.register("alice", self.password))
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(["--db", str(self.path), "set-role", "alice", "operator"]), 0)
            self.assertEqual(main(["--db", str(self.path), "list"]), 0)
            self.assertEqual(main(["--db", str(self.path), "set-role", "unknown", "operator"]), 1)
        self.assertEqual(self.store.role("alice"), "operator")
        self.assertIn("alice\toperator", output.getvalue())
        self.assertIn("Account not found", errors.getvalue())
        self.assertNotIn(self.password, output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
