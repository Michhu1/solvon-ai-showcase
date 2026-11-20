import pytest

from claims_assistant.documents import split_document
from claims_assistant.retrieval import BM25Retriever, tokenize
from claims_assistant.tools import ToolError


def test_documents_split_into_one_chunk_per_clause(chunks):
    clauses = [c.clause for c in chunks]
    assert len(clauses) == len(set(clauses)) == 20
    assert {c.product for c in chunks} == {"motor", "home", "travel"}
    glass = next(c for c in chunks if c.clause == "MOT-2.3")
    assert glass.section == "Glass damage" and "3,000 PLN" in glass.text


def test_split_document_keeps_text_until_the_next_heading():
    md = "# Doc\n\n### HOM-1.1 First\nline one\nline two\n\n### HOM-1.2 Second\nmore"
    first, second = split_document(md)
    assert first.text == "line one line two" and second.clause == "HOM-1.2"


def test_tokenizer_drops_stop_words_and_stems_plurals():
    assert tokenize("The pipes were leaking") == ["pipe", "leak"]


@pytest.mark.parametrize(
    ("query", "product", "expected"),
    [
        ("windscreen glass damage", "motor", "MOT-2.3"),
        ("water damage burst pipe leak", "home", "HOM-2.2"),
        ("baggage lost by airline", "travel", "TRV-2.2"),
        ("theft of the vehicle", "motor", "MOT-2.2"),
    ],
)
def test_bm25_ranks_the_right_clause_first(chunks, query, product, expected):
    hits = BM25Retriever(chunks).search(query, top_k=3, product=product)
    assert hits[0].chunk.clause == expected


def test_product_filter(chunks):
    hits = BM25Retriever(chunks).search("limit deductible", top_k=10, product="travel")
    assert hits and all(h.chunk.product == "travel" for h in hits)


def test_coverage_with_exclusion(toolbox):
    result = toolbox.check_coverage("motor", "collision", ["alcohol"])
    assert result["covered"] is False
    assert result["possible_exclusion"] == {"clause": "MOT-3.1", "circumstances": ["alcohol"]}
    assert {"MOT-2.1", "MOT-3.1"} <= set(toolbox.retrieved)


def test_payout_subtracts_deductible_and_caps_at_limit(toolbox):
    assert toolbox.estimate_payout("motor", "collision", 3850)["estimated_payout"] == 3350
    assert toolbox.estimate_payout("motor", "glass", 9000)["estimated_payout"] == 3000
    assert toolbox.estimate_payout("home", "water", 200)["estimated_payout"] == 0


def test_bad_tool_input_becomes_a_tool_error(toolbox):
    with pytest.raises(ToolError, match="Unknown cause"):
        toolbox.call("check_coverage", {"product": "motor", "cause": "meteor", "circumstances": []})
    with pytest.raises(ToolError, match="Unknown tool"):
        toolbox.call("delete_policy", {})
    with pytest.raises(ToolError, match="Invalid arguments"):
        toolbox.call("estimate_payout", {"product": "motor"})


def test_offline_extraction_edge_cases():
    from claims_assistant.llm import extract_amount, extract_date

    assert extract_date("on 31.02.2026") is None  # impossible date -> missing, not a crash
    assert extract_date("on 5 November 2026").isoformat() == "2026-11-05"
    assert extract_amount("cost 3.850 PLN") == 3850
    assert extract_amount("cost 12.50 PLN") == 12.5
    assert extract_amount("PLN 1 200") == 1200
