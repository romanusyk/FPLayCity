import os


def ensure_dir_exists(filepath: str) -> None:
    """Ensure the directory for the provided filepath exists."""
    directory = os.path.dirname(filepath)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

