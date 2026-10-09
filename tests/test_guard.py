import unittest

from support import ROOT  # noqa: F401  (puts the project on sys.path)

from app import guard


class GuardTest(unittest.TestCase):
    def test_accepts_one_select(self):
        for sql in [
            "select 1",
            "  SELECT a FROM t -- note\n",
            "with x as (select 1) select * from x;",
            "select ';' as semi, 'it''s' as q",
            "(select 1) union (select 2)",
            "select updated_at, create_time from t",
            'select "drop table" from t',
            "select $$a;b$$",
        ]:
            with self.subTest(sql=sql):
                self.assertTrue(guard.check(sql))

    def test_refuses_everything_else(self):
        backslash = chr(92)
        for sql in [
            "",
            "update t set a = 1",
            "delete from t",
            "insert into t values (1)",
            "create table x as select 1",
            "explain select 1",
            "select 1; drop table t",
            "select 1; -- x\nselect 2",
            "select 1;;",
            "select set_config('a', 'b', false)",
            "select pg_sleep(5)",
            "select pg_terminate_backend(1)",
            "select 1 /* open",
            "select 'open",
            "select E'" + backslash + "'' ; drop table x; --'",
        ]:
            with self.subTest(sql=sql):
                with self.assertRaises(guard.Rejected):
                    guard.check(sql)

    def test_trailing_semicolon_is_dropped(self):
        self.assertEqual(guard.check("select 1 ; "), "select 1")

    def test_personal_data_names(self):
        flagged = ["phone", "mobile_no", "email", "fcm_token", "otp", "pan", "company_pan_no", "pin", "aadhaar_number"]
        plain = ["pincode", "panel", "Tickets", "expand", "spin_count"]
        self.assertTrue(all(guard.is_pii_column(n) for n in flagged))
        self.assertFalse(any(guard.is_pii_column(n) for n in plain))


if __name__ == "__main__":
    unittest.main()
