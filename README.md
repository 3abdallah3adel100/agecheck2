# Meta Ad Set Age Targeting

Main file: `app_val_Final5.py`

Build marker shown in the app:

`2026-08-02-csv-business-filter-v2`

This build:

- Uses CSV snapshots only.
- Contains no `to_parquet()` or `read_parquet()` calls.
- Keeps Min Age and Max Age as text so `65+` is safe.
- Adds Business filter: El - Okaby / VAL / Direct / All.
- Keeps Business Unit, Agent, Ad Account, Active Campaign, Ad Set status and search filters.

After replacing the GitHub file, verify the build marker at the top of the Streamlit app. If the traceback still mentions `to_parquet`, Streamlit is deploying another branch or another main file.
