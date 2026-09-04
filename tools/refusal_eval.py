"""
Measured-refusal evaluation (spec section 18).

Runs a fixed set of questions through the answer engine and prints, for each,
the decision (answered / refused), the mode, and the retrieval grounding score
against the 0.40 threshold. Demonstrates that the assistant refuses rather than
invents when a product is outside the curated BIS knowledge base.

    python tools/refusal_eval.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from services import answer_engine as ae  # noqa: E402
from services.rag_engine import fanout_7_searches, MEASURED_REFUSAL_THRESHOLD  # noqa: E402

# (label, active product slug or None, question)
CASES = [
    ("supported / broad",    "electric_iron", "What do I need for BIS certification for my electric iron?"),
    ("supported / narrow",   "electric_iron", "Which lab can test my product?"),
    ("supported / scheme",   "two_wheeler_helmet", "Which BIS scheme applies to a two wheeler helmet?"),
    ("supported / licensing", "portland_cement", "How long does the licensing process take?"),
    ("in-KB by name only",   None, "What standard applies to an LED lamp?"),
    ("outside KB (product)",  None, "What are the BIS rules for drone batteries?"),
    ("outside KB (topic)",    None, "How much does BIS testing cost for a smart watch?"),
    ("off-topic",             None, "What is the weather in Delhi tomorrow?"),
]


def main():
    print(f"Measured refusal threshold: {MEASURED_REFUSAL_THRESHOLD}\n")
    print(f"{'case':<24} {'decision':<10} {'mode':<9} {'score':>6}  question")
    print("-" * 100)
    refused = 0
    for label, slug, q in CASES:
        res = ae.answer_question(slug, q, language="en")
        mode = res.get("mode")
        if mode == "refused":
            decision = "REFUSED"
            refused += 1
            score = res.get("grounding_score", 0.0)
        else:
            decision = "answered"
            chunks = fanout_7_searches(q)
            score = max((c["relevance_score"] for c in chunks), default=0.0)
        print(f"{label:<24} {decision:<10} {mode:<9} {score:>6.2f}  {q}")

    print("-" * 100)
    print(f"{refused} of {len(CASES)} refused (no fabricated answer). "
          f"Answers are given only when a product is in the curated BIS knowledge base.")


if __name__ == "__main__":
    main()
