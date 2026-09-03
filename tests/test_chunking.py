import unittest

from src.services.chunking import build_structured_chunks, normalize_document_ref


class ChunkingTests(unittest.TestCase):
    def test_preserves_article_and_clause_path(self):
        text = """CHƯƠNG I\nQuy định chung\nĐiều 1. Phạm vi\n1. Nội dung thứ nhất.\n2. Nội dung thứ hai."""
        chunks = build_structured_chunks(text, size=200, overlap=20, doc_key="doc-1")
        paths = [chunk.section_path for chunk in chunks]
        self.assertTrue(any("Điều 1" in path for path in paths))
        self.assertTrue(any("Khoản 1" in path for path in paths))
        self.assertEqual(list(range(len(chunks))), [chunk.chunk_index for chunk in chunks])

    def test_parent_id_is_stable(self):
        text = "Điều 2. Trách nhiệm\n1. Cơ quan A thực hiện.\n2. Cơ quan B phối hợp."
        first = build_structured_chunks(text, 100, 10, "same-doc")
        second = build_structured_chunks(text, 100, 10, "same-doc")
        self.assertEqual([item.parent_chunk_id for item in first], [item.parent_chunk_id for item in second])

    def test_normalizes_document_reference(self):
        self.assertEqual("230/QD-SKHCN", normalize_document_ref("230/QĐ - SKHCN"))


if __name__ == "__main__":
    unittest.main()
