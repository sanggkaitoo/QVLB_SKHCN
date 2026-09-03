import unittest

from src.agent.schemas import Evidence
from src.agent.verifier import evidence_is_sufficient


class VerifierTests(unittest.TestCase):
    def test_exact_evidence_is_sufficient(self):
        evidence = [Evidence(
            evidence_id="E1",
            text="Trích yếu văn bản",
            metadata={"retrieval_tool": "exact_document_search"},
            score=0.9,
        )]
        self.assertTrue(evidence_is_sufficient(evidence, exact_lookup=True))

    def test_semantic_requires_minimum_evidence(self):
        one = [Evidence(evidence_id="E1", text="Một nguồn", score=0.8)]
        self.assertFalse(evidence_is_sufficient(one, exact_lookup=False))


if __name__ == "__main__":
    unittest.main()
