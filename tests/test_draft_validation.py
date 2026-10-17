import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from draft_validation import shape_errors


def entry(entry_id="e-1", name="A"):
    return {"id": entry_id, "name": name, "kind": "character", "lead": "Fact.",
            "lead_refs": ["c1p1"], "sections": [{"heading": "Facts", "body": "Fact.", "refs": [1]}],
            "sources": [{"evidence_id": "c1p1"}], "links": [], "aliases": [],
            "reception": {"in_work": "", "user": "", "external": ""}, "stub": False}


class DraftValidationTests(unittest.TestCase):
    def test_valid_named_and_numeric_references(self):
        first, second = entry(), entry("e-2", "B")
        first["links"] = ["e-2"]
        self.assertEqual(shape_errors({"meta": {}, "entries": [first, second]}), [])

    def test_two_entries_report_their_errors_together_without_prose(self):
        first, second = entry(), entry("e-2", "B")
        first["lead"] = ""
        second["stub"] = "false"
        second["sections"][0]["body"] = ["private prose must not be printed"]
        errors = shape_errors({"entries": [first, second]})
        self.assertTrue(any(error.startswith("e-1/lead") for error in errors))
        self.assertTrue(any(error.startswith("e-2/stub") for error in errors))
        self.assertTrue(any(error.startswith("e-2/sections[1]/body") for error in errors))
        self.assertNotIn("private prose", " ".join(errors))

    def test_missing_or_unhashable_ids_have_indexed_errors(self):
        first, second = entry(), entry("e-2", "B")
        del first["id"]
        second["id"] = []
        errors = shape_errors({"entries": [first, second, None]})
        self.assertTrue(any(error.startswith("entry[1]/id") for error in errors))
        self.assertTrue(any(error.startswith("entry[2]/id") for error in errors))
        self.assertIn("entry[3] requires an object", errors)

    def test_bad_nested_shapes_and_reference_types_are_aggregated(self):
        value = entry()
        value.update(lead_refs="c1p1", sections=[None, {"body": 1, "refs": [False, 0, {}, ""]}],
                     sources=[None, {"quote": []}], links=[False], aliases={})
        errors = shape_errors({"entries": [value]})
        for field in ("lead_refs", "sections[1]", "sections[2]/body", "sources[1]", "sources[2]/quote", "links[1]", "aliases"):
            self.assertTrue(any(error.startswith("e-1/" + field) for error in errors), field)
        self.assertEqual(sum("sections[2]/refs[" in error for error in errors), 4)
        value = entry()
        value["sections"][0]["refs"] = {"id": "c1p1"}
        self.assertTrue(any("sections[1]/refs requires an array" in error
                            for error in shape_errors({"entries": [value]})))

    def test_duplicate_identity_and_name_are_both_reported(self):
        first, second = entry(), copy.deepcopy(entry())
        errors = shape_errors({"entries": [first, second]})
        self.assertIn("e-1/id duplicates entry[1]", errors)
        self.assertIn("e-1/name duplicates entry[1]", errors)

    def test_partial_batch_defers_only_link_resolution(self):
        value = entry()
        value["links"] = ["e-elsewhere"]
        self.assertEqual(shape_errors({"entries": [value]}, check_links=False), [])
        self.assertTrue(any("links[1] has no target" in error
                            for error in shape_errors({"entries": [value]})))
        value["links"] = [False]
        self.assertTrue(shape_errors({"entries": [value]}, check_links=False))

    def test_invalid_documents_and_required_arrays_return_errors(self):
        for doc in (None, [], {}, {"entries": None}, {"meta": [], "entries": []}):
            with self.subTest(doc=doc):
                self.assertTrue(shape_errors(doc))
        value = entry()
        del value["sections"]
        value["sources"] = "bad"
        value["kind"] = []
        value["reception"] = {"user": False}
        errors = shape_errors({"entries": [value]})
        for field in ("sections", "sources", "kind", "reception"):
            self.assertTrue(any(error.startswith("e-1/" + field) for error in errors), field)


if __name__ == "__main__":
    unittest.main()
