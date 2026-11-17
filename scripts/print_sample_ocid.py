"""Print one real live ocid with awards from the ingest store (used for manifest examples)."""
from bidbox.ingest import connect_store

with connect_store(read_only=True) as connection:
    row = connection.execute(
        "SELECT ocid, title, buyer_name, award_count FROM procurement_records "
        "WHERE origin='live' AND award_count > 0 ORDER BY release_sort DESC LIMIT 1"
    ).fetchone()
print(row)
