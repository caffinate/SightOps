import unittest

from desk.values import (
    dashed,
    decode,
    encode_property,
    normalise_id,
    page_id_from_url,
    parse_list,
    person_ids,
    relation_page_ids,
    to_row_value,
    values_equal,
)


class PageIdTests(unittest.TestCase):
    def test_extracts_from_every_url_shape(self):
        self.assertEqual(page_id_from_url("https://app.notion.com/3dbb26df54678170bcd4ec989e39d0bd"), "3dbb26df54678170bcd4ec989e39d0bd")
        self.assertEqual(page_id_from_url("https://app.notion.com/p/2420cd66f2274c2ba0bde1b42d30778c?pvs=204"), "2420cd66f2274c2ba0bde1b42d30778c")
        self.assertEqual(page_id_from_url("https://www.notion.so/Some-Title-3dbb26df54678170bcd4ec989e39d0bd"), "3dbb26df54678170bcd4ec989e39d0bd")
        self.assertIsNone(page_id_from_url(None))
        self.assertIsNone(page_id_from_url("https://app.notion.com/"))

    def test_normalises_user_and_dashed_ids(self):
        self.assertEqual(normalise_id("user://1cb13004-8266-40b6-92da-63771be9b248"), "1cb13004826640b692da63771be9b248")
        self.assertEqual(dashed("1cb13004826640b692da63771be9b248"), "1cb13004-8266-40b6-92da-63771be9b248")


class ListCodecTests(unittest.TestCase):
    def test_parse_list_accepts_json_string_list_or_scalar(self):
        self.assertEqual(parse_list('["a","b"]'), ["a", "b"])
        self.assertEqual(parse_list(["a"]), ["a"])
        self.assertEqual(parse_list(None), [])
        self.assertEqual(parse_list("Nathan"), ["Nathan"])

    def test_person_and_relation_ids(self):
        self.assertEqual(person_ids('["user://db244d9b-5d0e-4da3-8449-367242b98f17"]'), ["db244d9b5d0e4da38449367242b98f17"])
        self.assertEqual(relation_page_ids('["https://app.notion.com/1bf9cb6701ae49069c1b23e51d5d6f1f"]'), ["1bf9cb6701ae49069c1b23e51d5d6f1f"])


class DecodeTests(unittest.TestCase):
    def test_decodes_by_type(self):
        row = {"Owner": '["user://1cb13004-8266-40b6-92da-63771be9b248"]', "Tags": '["A","B"]',
               "date:Due Date:start": "2026-08-07", "date:Due Date:end": None, "date:Due Date:is_datetime": 0,
               "Status": "Done", "Notes": None, "Flag": "__YES__"}
        self.assertEqual(decode("person", row, "Owner"), ["1cb13004826640b692da63771be9b248"])
        self.assertEqual(decode("multi_select", row, "Tags"), ["A", "B"])
        self.assertEqual(decode("date", row, "Due Date"), {"start": "2026-08-07", "end": None, "is_datetime": False})
        self.assertEqual(decode("status", row, "Status"), "Done")
        self.assertIsNone(decode("text", row, "Notes"))
        self.assertTrue(decode("checkbox", row, "Flag"))


class EncodeTests(unittest.TestCase):
    def test_status_goes_through_raw(self):
        self.assertEqual(encode_property("Status", "status", "Not started"), {"Status": "Not started"})
        self.assertEqual(encode_property("Status", "status", None), {"Status": None})

    def test_date_expands_to_three_keys(self):
        self.assertEqual(encode_property("Due Date", "date", "2026-09-26"),
                         {"date:Due Date:start": "2026-09-26", "date:Due Date:end": None, "date:Due Date:is_datetime": 0})
        self.assertEqual(encode_property("Due Date", "date", {"start": "2026-09-04 02:45:00Z", "end": "2026-09-04 03:30:00Z", "is_datetime": True}),
                         {"date:Due Date:start": "2026-09-04 02:45:00Z", "date:Due Date:end": "2026-09-04 03:30:00Z", "date:Due Date:is_datetime": 1})
        self.assertEqual(encode_property("Due Date", "date", None)["date:Due Date:start"], None)

    def test_people_relations_and_multi_selects_are_lists(self):
        self.assertEqual(encode_property("Owner", "person", ["1cb13004826640b692da63771be9b248"]), {"Owner": ["1cb13004-8266-40b6-92da-63771be9b248"]})
        self.assertEqual(encode_property("Owner", "person", []), {"Owner": None})
        self.assertEqual(encode_property("Parent item", "relation", ["1bf9cb6701ae49069c1b23e51d5d6f1f"]),
                         {"Parent item": ["https://www.notion.so/1bf9cb6701ae49069c1b23e51d5d6f1f"]})
        self.assertEqual(encode_property("Tags", "multi_select", ["A", "B"]), {"Tags": ["A", "B"]})

    def test_checkbox_number_and_reserved_names(self):
        self.assertEqual(encode_property("Done", "checkbox", True), {"Done": "__YES__"})
        self.assertEqual(encode_property("Points", "number", "3"), {"Points": 3})
        self.assertEqual(encode_property("URL", "url", "https://x"), {"userDefined:URL": "https://x"})


class RowValueTests(unittest.TestCase):
    def test_row_shape_round_trips_through_decode(self):
        row = {}
        row["Owner"] = to_row_value("person", ["1cb13004826640b692da63771be9b248"])
        self.assertEqual(decode("person", row, "Owner"), ["1cb13004826640b692da63771be9b248"])
        cols = to_row_value("date", "2026-09-26")
        self.assertEqual(cols, {"start": "2026-09-26", "end": None, "is_datetime": 0})
        self.assertIsNone(to_row_value("multi_select", []))
        self.assertEqual(to_row_value("checkbox", False), "__NO__")


class EqualityTests(unittest.TestCase):
    def test_lists_compare_without_order_and_dates_compare_by_range(self):
        self.assertTrue(values_equal("person", ["a", "b"], ["b", "a"]))
        self.assertTrue(values_equal("date", "2026-09-26", {"start": "2026-09-26", "end": None}))
        self.assertFalse(values_equal("status", "Done", "done"))
        self.assertTrue(values_equal("text", None, ""))


if __name__ == "__main__":
    unittest.main()
