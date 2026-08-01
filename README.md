# AgeCheck2 — Dynamic Businesses + Date Range

Main file:

```text
app_val_Final5.py
```

Build marker shown inside the app:

```text
2026-08-02-dynamic-business-date-filter-v3
```

## What changed

- Discovers Businesses dynamically from `/me/businesses`.
- Also discovers owner Businesses from `/me/adaccounts?fields=business{id,name}`.
- Loads `owned_ad_accounts` and `client_ad_accounts` for every discovered Business.
- Does not restrict Ad Accounts to two hard-coded Business IDs.
- Does not hide Ad Accounts whose names do not match the known Agent patterns; they appear as Agent `Unknown`.
- Keeps the original Agent code mapping and filters.
- Adds Date Range options:
  - Today
  - Yesterday
  - Last 7 Days
  - This Month
  - Last Month
- The table still displays only:
  - Ad Account Name
  - Campaign Name
  - Ad Set Name
  - Min Age
  - Max Age
- Uses CSV snapshots only. There are no `to_parquet()` or `read_parquet()` calls.

## How the Date Range works

Age Min/Max are current Ad Set targeting settings and are not historical metrics.
To make the date filter meaningful, the app requests only minimal daily Insights fields internally:

```text
adset_id,date_start,impressions,spend
```

The selected date range then shows Ad Sets that had delivery in that period. No Spend or Impressions columns are displayed.

## Streamlit Secrets

```toml
APP_PASSWORD = "YOUR_LOGIN_PASSWORD"
META_ACCESS_TOKEN = "YOUR_META_ACCESS_TOKEN"
META_API_VERSION = "v26.0"

# Optional fallback only. This no longer limits discovery to these IDs.
# Leave empty when /me/businesses works correctly.
BUSINESS_IDS = []
```

## Permissions

The token needs access to the relevant advertising assets and `ads_read` for Ad Set targeting and Insights.
For automatic `/me/businesses` discovery, the token also needs the appropriate Business access and `business_management` permission.

Even if `/me/businesses` is unavailable, the app still uses `/me/adaccounts` and the Business metadata returned on directly accessible Ad Accounts.

## Deploy

1. Replace the old `app_val_Final5.py` in the GitHub repository.
2. Commit to the same branch used by Streamlit.
3. Confirm the main file path is `app_val_Final5.py`.
4. Reboot the app.
5. Confirm the build marker shown at the top is `2026-08-02-dynamic-business-date-filter-v3`.
6. Click **Refresh Age Data** once to build the new snapshot and date flags.

The Refresh button clears the Streamlit data cache before requesting Meta again, so newly granted Businesses and Ad Accounts are not hidden by the previous 30-minute cache.
