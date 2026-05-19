import logging

logger = logging.getLogger(__name__)


def validate_task_board(board: dict, valid_ids: list[int]) -> tuple[list, list]:
    valid_tasks = []
    flagged = []

    projects = board.get("projects", [])
    for project in projects:
        project_name = project.get("name", "Unknown")
        tasks = project.get("tasks", [])
        for task in tasks:
            source_id = task.get("source_msg_id")
            if source_id in valid_ids:
                task["project"] = project_name
                valid_tasks.append(task)
            else:
                flagged.append({
                    "message_id": source_id,
                    "reason": task.get("action", "Unknown task") + " — invalid or missing source_msg_id",
                })
                logger.warning(f"Task rejected — invalid source_msg_id {source_id}")

    board_flagged = board.get("flagged", [])
    for item in board_flagged:
        mid = item.get("message_id")
        if mid in valid_ids:
            flagged.append(item)
        else:
            flagged.append({
                "message_id": mid,
                "reason": item.get("reason", "Flagged item with invalid message_id"),
            })

    return valid_tasks, flagged
