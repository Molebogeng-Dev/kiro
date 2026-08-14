"""The marking prompt.

Kept in its own module because it is the part of this sprint most likely to be
tuned repeatedly, and because prompt changes deserve to show up clearly in a
diff rather than buried inside the calling code.

Two things it works hard at. First, output discipline: an open-weight model on a
free endpoint will add a friendly sentence before its JSON unless told several
times not to. Second, the feedback itself: "incorrect" is useless to a parent and
barely better for a learner, so the prompt asks for the misunderstanding behind
the mistake and one concrete next step.
"""

RESPONSE_SCHEMA = """{
  "overall": {"marks_awarded": <number>, "marks_available": <number>},
  "summary": "<two sentences a parent would understand>",
  "questions": [
    {
      "number": "<question number exactly as it appears on the paper>",
      "marks_awarded": <number>,
      "marks_available": <number>,
      "feedback": "<why marks were lost and what to do about it>"
    }
  ]
}"""

SYSTEM_PROMPT = """You are an experienced schoolteacher marking a learner's \
handwritten paper against an official memorandum.

How to mark:
- Read the handwriting in the image carefully before judging it. Learners' \
handwriting is uneven; do not mark an answer wrong because it is untidy.
- Mark only the questions that appear in the memorandum, and use the memorandum's \
marks as the maximum for each question.
- Award partial marks where the memorandum allows for them, including marks for \
correct method with an incorrect final answer.
- If a question was not attempted, or the answer is genuinely illegible, award 0 \
and say which of the two it was.
- Never award more marks than the memorandum makes available for a question.

How to write feedback:
- Explain WHY the marks were lost: name the misunderstanding, do not just say the \
answer was wrong.
- Add one concrete thing the learner should do differently next time.
- Write it so that a parent with no background in the subject understands it.
- Keep it to one or two plain sentences.
- Where full marks were earned, a short affirming note is enough.

Output rules, which matter as much as the marking:
- Reply with a single JSON object and nothing else.
- No markdown, no code fences, no explanation before or after the JSON.
- Use plain numbers for marks, not strings, and not fractions like "2/3".
"""


def build_user_prompt(memorandum) -> str:
    """Assemble the per-paper instruction from a Memorandum."""
    subject_line = (
        f"Subject: {memorandum.subject}\n" if memorandum.subject else ""
    )
    total_line = (
        f"Total marks available: {memorandum.total_marks}\n"
        if memorandum.total_marks
        else ""
    )

    return f"""Mark the learner's paper in the attached image against this memorandum.

{subject_line}Memorandum title: {memorandum.title}
{total_line}
--- MEMORANDUM START ---
{memorandum.content}
--- MEMORANDUM END ---

Return exactly this JSON structure:

{RESPONSE_SCHEMA}

Include one entry in "questions" for every question in the memorandum. Reply with \
the JSON object only."""


def build_retry_prompt(memorandum, error) -> str:
    """Re-ask after an unparseable reply, naming the specific problem.

    Telling the model what was wrong with its previous attempt works
    considerably better than sending the identical prompt again and hoping.
    """
    return f"""{build_user_prompt(memorandum)}

IMPORTANT: your previous reply could not be used. The problem was: {error}

Return only the raw JSON object. Do not wrap it in ``` fences. Do not write \
anything before or after it. Every question must include numeric marks_awarded \
and marks_available, and feedback explaining any marks lost."""
