"""Shared prompt fragments used across all chat modules."""

PROJECT_FILES_CONTEXT = (
    "\n\n# PROJECT FILE ACCESS\n\n"
    "You have access to the full project directory via your built-in file tools "
    "(read files, search, list directory). Use them freely to explore any part "
    "of the project when answering the user's questions.\n\n"
    "Project directory layout:\n"
    "  code/       - project source code\n"
    "  docs/       - project documents (markdown, text)\n"
    "  papers/     - research papers (PDF + extracted text + notes)\n"
    "  chats/      - general chat history\n\n"
    "You may read files from any of these directories, not just the one "
    "related to the current chat context. For example, a paper chat agent "
    "can read code files, and a code chat agent can read docs or papers.\n"
)
