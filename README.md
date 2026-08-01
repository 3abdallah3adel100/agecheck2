# Meta Ad Set Min / Max Age

This is a lightweight replacement for `app_val_Final5.py`.

It preserves the original:

- Password login.
- Business Manager account discovery.
- El - Okaby / Val Hair / VAL Booty classification.
- Agent codes and names.
- Account naming rules.
- Parallel refresh and snapshot handling.
- Business Unit, Agent, Ad Account and status filters.

It does **not** call Meta Ads Insights and does not pull:

- Spend
- Results
- CPL
- Impressions
- Clicks
- Gender breakdown
- Age performance breakdown
- Balance

It only loads the metadata required to show:

1. Ad Account Name
2. Campaign Name
3. Ad Set Name
4. Min Age
5. Max Age

## Streamlit main file

Set the Streamlit main file path to:

```text
app_val_Final5.py
```

## Streamlit Secrets

Paste the following into Streamlit Secrets:

```toml
APP_PASSWORD = "YOUR_PASSWORD"
META_ACCESS_TOKEN = "YOUR_META_ACCESS_TOKEN"
META_API_VERSION = "v26.0"

BUSINESS_IDS = [
    "751488620224306",
    "1178859133269743",
]
```

`BUSINESS_IDS` is optional because the same two IDs are already configured as defaults in the code.

## Agent mapping retained from the original file

```text
AA → Abdallah Adel
HM → Ahmed Hesham
BM → Bassem Shalawy
EK → Esraa Kamal
MA → Mahmoud
AF → Amr Fathy
SQ → (R)Ahmed Sharkawy
OS → (R)Osama Serwe
MM → (R)Mohamed Mahmoud
NB → (R)Mohamed Nabih
```

The code is extracted from the Ad Account name with the same boundary-aware regular expression used by the original application.

## Date ranges

Min Age and Max Age are current Ad Set targeting settings, not historical Insights metrics. Therefore this version intentionally does not include Today, Yesterday, Last 7 Days, This Month or Last Month.

Adding a date filter would require an additional Insights request to determine which Ad Sets delivered during that period, which would contradict the requirement to pull only current age-targeting data.
