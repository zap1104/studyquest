"""Answer grading, shared by the chapter quiz and Dungeon Quest.

Lifted verbatim out of ``courses/views.py`` so the dungeon grades with the exact
rules the quiz uses — one grader, one normalization pass, no second
implementation to drift. ``courses.views`` re-exports both names, so existing
imports from there keep working.
"""

import re
import unicodedata


def normalize_text_answer(value):
    """
    Deterministic normalization for Identification and Enumeration.
    Avoids fuzzy matching to prevent credit on incorrect technical terms.
    """
    value = unicodedata.normalize("NFKC", value or "")
    value = value.casefold()
    value = re.sub(r"[^\w\s.]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().strip(".")


def _grade_question(question, submitted_answer):
    max_points = question.max_points()
    if not isinstance(submitted_answer, dict):
        submitted_answer = {}

    # Choice-based grading
    if question.question_type in {"multiple_choice", "true_false"}:
        chosen_choice_id = submitted_answer.get("choice_id") if submitted_answer else None
        correct_choice = next((c for c in question.choices.all() if c.is_correct), None)
        try:
            chosen_choice_id = int(chosen_choice_id)
        except (TypeError, ValueError):
            chosen_choice_id = None
        is_correct = chosen_choice_id is not None and correct_choice is not None and chosen_choice_id == correct_choice.id
        return (
            1 if is_correct else 0,
            max_points,
            {
                "is_correct": is_correct,
                "correct_choice_id": correct_choice.id if correct_choice else None,
                "correct_choice_text": correct_choice.text if correct_choice else "",
            },
        )

    # Identification grading
    if question.question_type == "identification":
        submitted_text = (submitted_answer or {}).get("text", "")
        if not isinstance(submitted_text, str):
            submitted_text = ""
        accepted_answers = question.answer_data.get("accepted_answers", [])
        normalized_submitted = normalize_text_answer(submitted_text)
        is_correct = any(
            normalize_text_answer(a) == normalized_submitted for a in accepted_answers
        )
        return (
            1 if is_correct else 0,
            max_points,
            {
                "is_correct": is_correct,
                "accepted_answers": accepted_answers,
                "canonical_answer": accepted_answers[0] if accepted_answers else "",
            },
        )

    # Enumeration grading
    if question.question_type == "enumeration":
        submitted_items = (submitted_answer or {}).get("items", [])
        if not isinstance(submitted_items, list):
            submitted_items = []
        expected_items = list(question.answer_data.get("expected_items", []))
        order_matters = bool(question.answer_data.get("order_matters", False))

        if order_matters:
            matched_items = []
            for index, expected in enumerate(expected_items):
                if index >= len(submitted_items):
                    break
                sub_norm = normalize_text_answer(submitted_items[index])
                candidates = [expected["canonical"]] + expected.get("accepted_variants", [])
                if any(normalize_text_answer(c) == sub_norm for c in candidates):
                    matched_items.append(expected["canonical"])

            missing_items = [
                exp["canonical"] for exp in expected_items if exp["canonical"] not in matched_items
            ]
        else:
            remaining = list(expected_items)
            matched_items = []
            seen_submissions = set()

            for raw_item in submitted_items:
                normalized = normalize_text_answer(raw_item)
                if not normalized or normalized in seen_submissions:
                    continue
                seen_submissions.add(normalized)

                match_index = None
                for index, expected in enumerate(remaining):
                    candidates = [expected["canonical"]] + expected.get("accepted_variants", [])
                    if any(normalize_text_answer(c) == normalized for c in candidates):
                        match_index = index
                        break

                if match_index is not None:
                    matched_items.append(remaining[match_index]["canonical"])
                    remaining.pop(match_index)

            missing_items = [expected["canonical"] for expected in remaining]

        earned_points = len(matched_items)
        return (
            earned_points,
            max_points,
            {
                "is_correct": (earned_points == max_points),
                "matched_items": matched_items,
                "missing_items": missing_items,
                "order_matters": order_matters,
            },
        )

    return (0, max_points, {"is_correct": False})
