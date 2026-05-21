CONVERT_PROMPT = """AVAILABLE TIME: {time_minutes} minutes

[TIER 2 SUMMARY — compressed history of past sessions]
{tier2_summary}

[RELATED PAST THOUGHTS — semantically similar to today's inbox]
{related_thoughts}

[CARRY-OVER SEED — next action from yesterday's log]
{seed_action}

[TIER 1 — all messages since last conversion, with IDs]
{tier1_messages}

TASK:
Convert the inbox into an executable task board for the available time window.
Fit blocks within the time window. Do not overfill.

EXAMPLES OF GOOD TASKS (mix of types):
1. {{ "action": "Visit wener to diagnose the new graphics designer's PC issues",
    "source_msg_id": 101, "estimated_mins": 60, "block": 1 }}
2. {{ "action": "Write the industry vertical filter function in retrieval.py",
    "source_msg_id": 142, "estimated_mins": 45, "block": 2 }}
3. {{ "action": "Message Put to ask when she's coming back",
    "source_msg_id": 108, "estimated_mins": 5, "block": 3 }}

EXAMPLES OF BAD TASKS:
- {{ "action": "Work on stuff", ... }}  — too vague, no verb, no scope
- {{ "action": "Follow up", ... }}  — missing who and about what

OUTPUT: Valid JSON only. No markdown. No preamble. No explanation.
{{
  "projects": [
    {{
      "name": "string",
      "tasks": [
        {{
          "action": "string — verb-first, specific, describes what to actually do",
          "source_msg_id": integer,
          "estimated_mins": integer,
          "block": integer
        }}
      ]
    }}
  ],
  "flagged": [
    {{ "message_id": integer, "reason": "string — specific missing detail" }}
  ],
  "related_surfaced": ["string — past thought worth noting today"],
  "summary": "string — one-paragraph summary of this session to use as Tier 2 memory for future sessions"
}}"""
