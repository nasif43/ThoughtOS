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
Select 1-3 ideas maximum. Break each into atomic steps.
Fit blocks within the time window. Do not overfill.

EXAMPLE OF A GOOD TASK:
{{ "action": "Write the industry vertical filter function in retrieval.py",
  "source_msg_id": 142, "estimated_mins": 45, "block": 1 }}

EXAMPLE OF A BAD TASK (too vague, no verb, no file, no scope):
{{ "action": "Work on the filter", "source_msg_id": 142, ... }}

OUTPUT: Valid JSON only. No markdown. No preamble. No explanation.
{{
  "projects": [
    {{
      "name": "string",
      "tasks": [
        {{
          "action": "string — verb-first, specific, names files or components",
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
  "related_surfaced": ["string — past thought worth noting today"]
}}"""
