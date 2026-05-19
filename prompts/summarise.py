SUMMARISE_PROMPT = """Compress the following session into a single paragraph that summarises
the key ideas, tasks, and themes. This will be used as long-term context
for future sessions. Keep it concise but preserve specific project names,
decisions, and technical details.

PREVIOUS SUMMARY:
{previous_summary}

SESSION MESSAGES:
{session_messages}

OUTPUT: A single paragraph. No preamble. No markdown."""
