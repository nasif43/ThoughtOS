LOG_PROMPT = """[TODAY'S TASK BOARD]
{task_board_json}

[USER'S LOG INPUT]
{raw_log_text}

TASK:
Parse the log input into structured fields.
Diagnose which layer failed based only on evidence in the log — do not infer.

L2 failure indicators: tasks were too large, wrong priority, or not in the log at all.
L3 failure indicators: explicit mention of drift, switching tasks, not using timer,
  working on something not on the board.
L4 failure indicators: log is very late, very sparse, or missing fields entirely.

OUTPUT: Valid JSON only. No markdown. No preamble.
{{
  "planned": "string",
  "shipped": "string",
  "failed": "string",
  "next_action": "string — single executable step, verb-first",
  "diagnosis": "string — 1-2 sentences, evidence-based only",
  "layer_failed": "L2 | L3 | L4 | none",
  "pattern_warning": "string | null"
}}"""
