import os


class Season:

    s2425 = '2024-2025'
    s2526 = '2025-2026'


def ensure_dir_exists(filepath: str) -> None:
    """Ensure the directory for the provided filepath exists."""
    directory = os.path.dirname(filepath)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

