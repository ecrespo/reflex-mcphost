"""Forward-only schema migrations (Data Model §5).

Each module ``NNNN_<slug>.py`` exposes ``VERSION: int`` and
``up(conn: sqlite3.Connection) -> None``; the runner applies pending ones in order.
"""
