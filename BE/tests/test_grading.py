from app.domains.retrieval.grading import grade_documents
from app.domains.retrieval.hybrid import RetrievedChunk


def test_grade_documents_correct_for_strong_overlap():
    query = "python unit testing fixtures"
    chunks = [
        RetrievedChunk(
            chunk_id=1,
            text="Python unit testing uses fixtures to isolate state.",
            video_stem="doc1",
        )
    ]

    assert grade_documents(query, chunks) == "correct"


def test_grade_documents_ambiguous_for_partial_overlap():
    query = "python web framework testing database"
    chunks = ["Python cookbook recipes for clean code."]

    assert grade_documents(query, chunks) == "ambiguous"


def test_grade_documents_wrong_for_empty_or_unrelated_chunks():
    assert grade_documents("python testing", []) == "wrong"
    assert grade_documents("python testing", ["astronomy telescope nebula"]) == "wrong"
