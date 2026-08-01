import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


st.set_page_config(page_title="Meta Ad Set Age Targeting", layout="wide")


# =========================================================
# Login — same secret name used by the original application
# =========================================================
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.title("🔐 Login Required")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if password == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("Wrong password")

    return False


if not check_password():
    st.stop()


# =========================================================
# Meta configuration — keeps the original secret names
# =========================================================
BASE_URL = "https://graph.facebook.com"
API_VERSION = st.secrets.get("META_API_VERSION", "v26.0")
ACCESS_TOKEN = st.secrets["META_ACCESS_TOKEN"]

DEFAULT_BUSINESS_IDS = [
    "751488620224306",   # El - Okaby
    "1178859133269743",  # VAL
]

try:
    configured_business_ids = st.secrets.get("BUSINESS_IDS", DEFAULT_BUSINESS_IDS)
    if isinstance(configured_business_ids, str):
        BUSINESS_IDS = [
            value.strip()
            for value in configured_business_ids.split(",")
            if value.strip()
        ]
    else:
        BUSINESS_IDS = [
            str(value).strip()
            for value in configured_business_ids
            if str(value).strip()
        ]
except Exception:
    BUSINESS_IDS = DEFAULT_BUSINESS_IDS

REFRESH_LOCK_MAX_AGE_SECONDS = 10 * 60

DATA_DIR = Path("app_data")
DATA_DIR.mkdir(exist_ok=True)

AGE_TARGETING_FILE = DATA_DIR / "age_targeting_min_max_snapshot.parquet"
ACCOUNTS_FILE = DATA_DIR / "age_targeting_accounts_snapshot.parquet"
RAW_ACCOUNTS_FILE = DATA_DIR / "age_targeting_raw_accounts_snapshot.parquet"
META_FILE = DATA_DIR / "age_targeting_meta_snapshot.json"
LOCK_FILE = DATA_DIR / "age_targeting_refresh.lock"

TMP_AGE_TARGETING_FILE = DATA_DIR / "age_targeting_min_max_snapshot.tmp.parquet"
TMP_ACCOUNTS_FILE = DATA_DIR / "age_targeting_accounts_snapshot.tmp.parquet"
TMP_RAW_ACCOUNTS_FILE = DATA_DIR / "age_targeting_raw_accounts_snapshot.tmp.parquet"
TMP_META_FILE = DATA_DIR / "age_targeting_meta_snapshot.tmp.json"


# =========================================================
# Same agent coding used in app_val_Final5.py
# =========================================================
MEDIA_BUYER_MAP = {
    "AA": "Abdallah Adel",
    "HM": "Ahmed Hesham",
    "BM": "Bassem Shalawy",
    "EK": "Esraa Kamal",
    "MA": "Mahmoud",
    "AF": "Amr Fathy",
    "SQ": "(R)Ahmed Sharkawy",
    "OS": "(R)Osama Serwe",
    "MM": "(R)Mohamed Mahmoud",
    "NB": "(R)Mohamed Nabih",
}


# =========================================================
# Text / classification helpers — copied from original logic
# =========================================================
def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip().upper()


def normalize_account_id(account_id):
    if account_id is None:
        return ""
    return str(account_id).replace("act_", "")


def extract_buyer_code(account_name: str) -> str:
    text = normalize_text(account_name)
    for code in MEDIA_BUYER_MAP.keys():
        pattern = rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])"
        if re.search(pattern, text):
            return code
    return "UNKNOWN"


def is_relevant_account_name(account_name: str) -> bool:
    text = normalize_text(account_name)
    return (
        "OK-FB-HR-" in text
        or "OK-FB-NF-" in text
        or "US-FB-HR-" in text
        or "US-BO-HR-" in text
    )


def source_is_okaby(source: str) -> bool:
    return "751488620224306" in str(source)


def detect_business_unit(account_name="", campaign_name="", source=""):
    acc = normalize_text(account_name)
    camp = normalize_text(campaign_name)
    src = str(source)

    if "OK-FB-HR-" in acc or "OK-FB-NF-" in acc:
        return "El - Okaby"

    if "US-FB-HR-" in acc:
        return "Val Hair"

    if "US-BO-HR-" in acc:
        return "VAL Booty"

    # Same original fallback: all accounts from El-Okaby business remain visible,
    # even when their account name does not contain a known agent code.
    if source_is_okaby(src):
        return "El - Okaby"

    if re.search(
        r"(?<![A-Z0-9])(?:AA|HM|BM|EK|MA|AF|SQ|OS|MM|NB)-FB-HR(?![A-Z0-9])",
        camp,
    ):
        return "Val Hair"

    if re.search(
        r"(?<![A-Z0-9])(?:AA|HM|BM|EK|MA|AF|SQ|OS|MM|NB)-FB-BO(?![A-Z0-9])",
        camp,
    ):
        return "VAL Booty"

    return "Unknown"


def campaign_status_label(status=None, effective_status=None):
    raw = normalize_text(effective_status) or normalize_text(status)
    if raw == "ACTIVE":
        return "Active"
    if raw:
        return "Not Active"
    return "Unknown"


def adset_status_label(status=None, effective_status=None):
    raw = normalize_text(effective_status) or normalize_text(status)
    if raw == "ACTIVE":
        return "Active"
    if raw:
        return "Not Active"
    return "Unknown"


def format_min_age(targeting):
    if not isinstance(targeting, dict):
        return "Not returned"

    value = targeting.get("age_min")
    if value in (None, ""):
        return "Not returned"

    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def format_max_age(targeting):
    if not isinstance(targeting, dict):
        return "Not returned"

    value = targeting.get("age_max")
    if value in (None, ""):
        return "Not returned"

    try:
        numeric = int(value)
        return "65+" if numeric >= 65 else numeric
    except (TypeError, ValueError):
        return str(value)


# =========================================================
# Safe API client and pagination
# =========================================================
class MetaAPIError(RuntimeError):
    def __init__(self, message, code=None, subcode=None, trace_id=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.trace_id = trace_id

    def display_text(self):
        details = []
        if self.code is not None:
            details.append(f"Code {self.code}")
        if self.subcode is not None:
            details.append(f"Subcode {self.subcode}")
        if self.trace_id:
            details.append(f"Trace {self.trace_id}")
        suffix = f" ({' | '.join(details)})" if details else ""
        return f"{self.message}{suffix}"


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_all_pages(url, params=None):
    all_rows = []

    while True:
        try:
            response = requests.get(url, params=params, timeout=90)
        except requests.RequestException as exc:
            raise MetaAPIError(f"Network error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MetaAPIError(
                f"Meta returned a non-JSON response (HTTP {response.status_code})."
            ) from exc

        if response.status_code >= 400 or "error" in payload:
            error = payload.get("error", {})
            raise MetaAPIError(
                error.get("message", f"Meta API HTTP {response.status_code}"),
                code=error.get("code"),
                subcode=error.get("error_subcode"),
                trace_id=error.get("fbtrace_id"),
            )

        all_rows.extend(payload.get("data", []))

        next_url = payload.get("paging", {}).get("next")
        if not next_url:
            break

        url = next_url
        params = None

    return all_rows


@st.cache_data(ttl=1800, show_spinner=False)
def get_ad_accounts():
    all_dfs = []
    errors = []

    sources = [
        ("me/adaccounts", f"{BASE_URL}/{API_VERSION}/me/adaccounts"),
    ]

    for business_id in BUSINESS_IDS:
        sources.extend(
            [
                (
                    f"business/{business_id}/owned_ad_accounts",
                    f"{BASE_URL}/{API_VERSION}/{business_id}/owned_ad_accounts",
                ),
                (
                    f"business/{business_id}/client_ad_accounts",
                    f"{BASE_URL}/{API_VERSION}/{business_id}/client_ad_accounts",
                ),
            ]
        )

    for source_name, url in sources:
        try:
            params = {
                "fields": "id,account_id,name,account_status,currency",
                "access_token": ACCESS_TOKEN,
                "limit": 500,
            }
            rows = fetch_all_pages(url, params)
            df = pd.DataFrame(rows)
            if not df.empty:
                df["source"] = source_name
                all_dfs.append(df)
        except Exception as exc:
            message = exc.display_text() if isinstance(exc, MetaAPIError) else str(exc)
            errors.append(f"{source_name}: {message}")

    if not all_dfs:
        return pd.DataFrame(), pd.DataFrame(), errors

    raw_accounts = pd.concat(all_dfs, ignore_index=True)

    # Same account inclusion logic as app_val_Final5.py.
    if "name" in raw_accounts.columns:
        name_match = raw_accounts["name"].apply(is_relevant_account_name)
        okaby_source = raw_accounts["source"].apply(source_is_okaby)
        raw_accounts = raw_accounts[name_match | okaby_source].copy()

    dedup = raw_accounts.copy()
    if "id" in dedup.columns:
        dedup = (
            dedup.sort_values(["name", "source"])
            .drop_duplicates(subset=["id"], keep="first")
        )
    elif "account_id" in dedup.columns:
        dedup = (
            dedup.sort_values(["name", "source"])
            .drop_duplicates(subset=["account_id"], keep="first")
        )

    return dedup.reset_index(drop=True), raw_accounts.reset_index(drop=True), errors


@st.cache_data(ttl=1800, show_spinner=False)
def get_campaigns(account_id):
    clean_id = normalize_account_id(account_id)
    url = f"{BASE_URL}/{API_VERSION}/act_{clean_id}/campaigns"
    params = {
        "fields": "id,name,status,effective_status",
        "access_token": ACCESS_TOKEN,
        "limit": 1000,
    }
    rows = fetch_all_pages(url, params)
    return pd.DataFrame(rows)


@st.cache_data(ttl=1800, show_spinner=False)
def get_adsets_with_targeting(account_id):
    clean_id = normalize_account_id(account_id)
    url = f"{BASE_URL}/{API_VERSION}/act_{clean_id}/adsets"
    params = {
        # No Insights, spend, results, gender, balance, actions or breakdowns.
        # Only Ad Set metadata plus the targeting object that contains age_min/age_max.
        "fields": "id,name,campaign_id,status,effective_status,targeting",
        "access_token": ACCESS_TOKEN,
        "limit": 1000,
    }
    rows = fetch_all_pages(url, params)
    return pd.DataFrame(rows)


def fetch_one_account(row):
    account_id = row.get("id") or row.get("account_id")
    account_name = row.get("name", "Unknown")
    source = row.get("source", "")

    try:
        campaigns_df = get_campaigns(account_id)
        adsets_df = get_adsets_with_targeting(account_id)

        if adsets_df.empty:
            return {
                "rows": pd.DataFrame(),
                "account_name": account_name,
                "error": None,
            }

        campaign_map = {}
        if not campaigns_df.empty:
            for _, campaign in campaigns_df.iterrows():
                campaign_id = str(campaign.get("id", ""))
                campaign_map[campaign_id] = {
                    "name": campaign.get("name", "Unknown Campaign"),
                    "status": campaign.get("status"),
                    "effective_status": campaign.get("effective_status"),
                }

        buyer_code = extract_buyer_code(account_name)
        media_buyer = MEDIA_BUYER_MAP.get(buyer_code, "Unknown")

        output_rows = []
        for _, adset in adsets_df.iterrows():
            campaign_id = str(adset.get("campaign_id", ""))
            campaign = campaign_map.get(campaign_id, {})
            campaign_name = campaign.get("name", "Unknown Campaign")
            targeting = adset.get("targeting")
            if not isinstance(targeting, dict):
                targeting = {}

            output_rows.append(
                {
                    "business_unit": detect_business_unit(
                        account_name=account_name,
                        campaign_name=campaign_name,
                        source=source,
                    ),
                    "buyer_code": buyer_code,
                    "media_buyer": media_buyer,
                    "account_id": str(account_id),
                    "account_name": account_name,
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "campaign_status": campaign_status_label(
                        campaign.get("status"),
                        campaign.get("effective_status"),
                    ),
                    "adset_id": str(adset.get("id", "")),
                    "adset_name": adset.get("name", "Unknown Ad Set"),
                    "adset_status": adset_status_label(
                        adset.get("status"),
                        adset.get("effective_status"),
                    ),
                    "min_age": format_min_age(targeting),
                    "max_age": format_max_age(targeting),
                }
            )

        return {
            "rows": pd.DataFrame(output_rows),
            "account_name": account_name,
            "error": None,
        }

    except Exception as exc:
        message = exc.display_text() if isinstance(exc, MetaAPIError) else str(exc)
        return {
            "rows": pd.DataFrame(),
            "account_name": account_name,
            "error": f"{account_name}: {message}",
        }


# =========================================================
# Snapshot persistence
# =========================================================
def snapshot_exists():
    return AGE_TARGETING_FILE.exists() and META_FILE.exists()


def safe_read_parquet(path):
    try:
        if path.exists():
            return pd.read_parquet(path)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    return pd.DataFrame()


def load_snapshot():
    if not snapshot_exists():
        return None

    try:
        with open(META_FILE, "r", encoding="utf-8") as file:
            meta = json.load(file)
    except Exception:
        return None

    return {
        "age_targeting": safe_read_parquet(AGE_TARGETING_FILE),
        "accounts": safe_read_parquet(ACCOUNTS_FILE),
        "raw_accounts": safe_read_parquet(RAW_ACCOUNTS_FILE),
        "meta": meta,
    }


def save_snapshot_atomic(age_targeting_df, accounts_df, raw_accounts_df, meta):
    age_targeting_df.to_parquet(TMP_AGE_TARGETING_FILE, index=False)
    accounts_df.to_parquet(TMP_ACCOUNTS_FILE, index=False)
    raw_accounts_df.to_parquet(TMP_RAW_ACCOUNTS_FILE, index=False)

    with open(TMP_META_FILE, "w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)

    os.replace(TMP_AGE_TARGETING_FILE, AGE_TARGETING_FILE)
    os.replace(TMP_ACCOUNTS_FILE, ACCOUNTS_FILE)
    os.replace(TMP_RAW_ACCOUNTS_FILE, RAW_ACCOUNTS_FILE)
    os.replace(TMP_META_FILE, META_FILE)


def build_account_sources_table(raw_accounts):
    if raw_accounts.empty:
        return raw_accounts

    id_col = "id" if "id" in raw_accounts.columns else "account_id"
    base_cols = [col for col in [id_col, "name"] if col in raw_accounts.columns]

    source_table = (
        raw_accounts.groupby(base_cols, dropna=False)["source"]
        .apply(lambda values: " | ".join(sorted(set(map(str, values)))))
        .reset_index(name="sources")
    )
    return source_table


# =========================================================
# Refresh lock — same safe refresh approach as original
# =========================================================
def get_lock_info():
    if not LOCK_FILE.exists():
        return None

    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        created_at = float(data.get("created_at", 0))
        data["age_seconds"] = max(0.0, time.time() - created_at)
        return data
    except Exception:
        return {"age_seconds": None}


def is_refresh_locked():
    info = get_lock_info()
    if not info:
        return False

    age = info.get("age_seconds")
    if age is not None and age > REFRESH_LOCK_MAX_AGE_SECONDS:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    return True


def acquire_refresh_lock():
    if is_refresh_locked():
        return False

    try:
        with open(LOCK_FILE, "x", encoding="utf-8") as file:
            json.dump({"created_at": time.time()}, file)
        return True
    except FileExistsError:
        return False


def release_refresh_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def force_clear_refresh_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
        return True
    except Exception:
        return False


# =========================================================
# UI
# =========================================================
snapshot = load_snapshot()

st.title("Meta Ad Set Min / Max Age")
st.caption(
    "Same Ad Account → Agent coding as app_val_Final5.py. "
    "This app does not request Insights, Spend, Results, Gender, Balance or Age breakdowns."
)

with st.sidebar:
    st.header("Data Load")
    max_workers = st.slider(
        "Parallel workers",
        min_value=2,
        max_value=10,
        value=4,
        step=2,
    )
    show_account_sources = st.checkbox("Show account sources", value=False)
    refresh_clicked = st.button("Refresh Age Data", use_container_width=True)
    force_unlock_clicked = st.button(
        "Clear stuck refresh lock",
        use_container_width=True,
    )

if force_unlock_clicked:
    force_clear_refresh_lock()
    st.success("Refresh lock cleared. You can click Refresh Age Data now.")

if refresh_clicked:
    if not acquire_refresh_lock():
        info = get_lock_info() or {}
        age = info.get("age_seconds")
        if age is not None:
            st.warning(
                "Refresh is already running or was recently interrupted. "
                f"Lock age: {age / 60:.1f} minutes."
            )
        else:
            st.warning("Refresh is already running.")
        st.stop()

    try:
        with st.status("Refreshing current Ad Set age targeting...", expanded=True) as status:
            accounts_df, raw_accounts_df, account_discovery_errors = get_ad_accounts()
            status.write(
                f"Loaded {len(accounts_df)} relevant Ad Accounts from configured businesses."
            )

            if accounts_df.empty:
                st.error("No matching El-Okaby / VAL Ad Accounts were found.")
                if account_discovery_errors:
                    st.code("\n".join(account_discovery_errors))
                st.stop()

            all_rows = []
            errors = list(account_discovery_errors)
            progress = st.progress(0)
            progress_text = st.empty()
            started_at = time.time()
            total = len(accounts_df)
            done = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(fetch_one_account, row)
                    for _, row in accounts_df.iterrows()
                ]

                for future in as_completed(futures):
                    result = future.result()

                    if result["error"]:
                        errors.append(result["error"])

                    if not result["rows"].empty:
                        all_rows.append(result["rows"])

                    done += 1
                    elapsed = time.time() - started_at
                    progress.progress(done / total)
                    progress_text.info(
                        f"Refreshing accounts: {done}/{total} | "
                        f"Last finished: {result.get('account_name', '-')} | "
                        f"Elapsed: {elapsed:.1f}s | Errors: {len(errors)}"
                    )

            age_targeting_df = (
                pd.concat(all_rows, ignore_index=True)
                if all_rows
                else pd.DataFrame(
                    columns=[
                        "business_unit",
                        "buyer_code",
                        "media_buyer",
                        "account_id",
                        "account_name",
                        "campaign_id",
                        "campaign_name",
                        "campaign_status",
                        "adset_id",
                        "adset_name",
                        "adset_status",
                        "min_age",
                        "max_age",
                    ]
                )
            )

            if not age_targeting_df.empty:
                age_targeting_df = age_targeting_df.sort_values(
                    [
                        "business_unit",
                        "media_buyer",
                        "account_name",
                        "campaign_name",
                        "adset_name",
                    ],
                    kind="stable",
                ).reset_index(drop=True)

            meta = {
                "last_fetch_ts": pd.Timestamp.utcnow().isoformat(),
                "accounts_count": int(accounts_df["id"].nunique())
                if "id" in accounts_df.columns
                else int(len(accounts_df)),
                "adsets_count": int(len(age_targeting_df)),
                "business_ids": BUSINESS_IDS,
                "errors_count": int(len(errors)),
                "errors": errors[:100],
                "data_scope": "Current Ad Set targeting only; no historical Insights requested.",
            }

            save_snapshot_atomic(
                age_targeting_df,
                accounts_df,
                build_account_sources_table(raw_accounts_df),
                meta,
            )

            st.cache_data.clear()
            status.update(label="Refresh complete. New age snapshot saved.", state="complete")
            st.rerun()

    except Exception as exc:
        st.error(f"Refresh failed: {exc}")
        raise
    finally:
        release_refresh_lock()


if not snapshot:
    st.info("No saved Age snapshot yet. Click Refresh Age Data.")
    st.stop()


age_df = snapshot["age_targeting"].copy()
accounts_df = snapshot["accounts"].copy()
raw_accounts_df = snapshot["raw_accounts"].copy()
meta = snapshot["meta"]

st.caption(
    f"Last updated: {meta.get('last_fetch_ts', '-')}"
    f" | Fetched accounts: {meta.get('accounts_count', 0)}"
    f" | Ad Sets: {meta.get('adsets_count', 0)}"
    f" | Errors: {meta.get('errors_count', 0)}"
)

# Business Unit remains on the upper right, like the original dashboard.
left_title_col, right_filter_col = st.columns([3, 1])
with left_title_col:
    st.subheader("Filters")
with right_filter_col:
    selected_business_unit = st.selectbox(
        "Business Unit",
        ["El - Okaby", "Val Hair", "VAL Booty", "All"],
        index=0,
        key="business_unit_selector",
    )

filtered = age_df.copy()
if selected_business_unit != "All" and not filtered.empty:
    filtered = filtered[filtered["business_unit"] == selected_business_unit].copy()

filter_col_1, filter_col_2, filter_col_3, filter_col_4 = st.columns(4)

agent_options = (
    sorted(filtered["media_buyer"].dropna().astype(str).unique().tolist())
    if not filtered.empty
    else []
)
with filter_col_1:
    selected_agent = st.selectbox(
        "Media Buyer / Agent",
        ["All"] + agent_options,
        index=0,
    )

if selected_agent != "All" and not filtered.empty:
    filtered = filtered[filtered["media_buyer"] == selected_agent].copy()

account_options = (
    sorted(filtered["account_name"].dropna().astype(str).unique().tolist())
    if not filtered.empty
    else []
)
with filter_col_2:
    selected_account = st.selectbox(
        "Ad Account",
        ["All"] + account_options,
        index=0,
    )

if selected_account != "All" and not filtered.empty:
    filtered = filtered[filtered["account_name"] == selected_account].copy()

campaign_status_options = (
    sorted(filtered["campaign_status"].dropna().astype(str).unique().tolist())
    if not filtered.empty
    else []
)
with filter_col_3:
    selected_campaign_status = st.selectbox(
        "Campaign Status",
        ["All"] + campaign_status_options,
        index=0,
    )

if selected_campaign_status != "All" and not filtered.empty:
    filtered = filtered[
        filtered["campaign_status"] == selected_campaign_status
    ].copy()

adset_status_options = (
    sorted(filtered["adset_status"].dropna().astype(str).unique().tolist())
    if not filtered.empty
    else []
)
with filter_col_4:
    selected_adset_status = st.selectbox(
        "Ad Set Status",
        ["All"] + adset_status_options,
        index=0,
    )

if selected_adset_status != "All" and not filtered.empty:
    filtered = filtered[filtered["adset_status"] == selected_adset_status].copy()

search_col_1, search_col_2 = st.columns(2)
with search_col_1:
    campaign_search = st.text_input(
        "Search Campaign",
        placeholder="Type part of the Campaign name...",
    ).strip()
with search_col_2:
    adset_search = st.text_input(
        "Search Ad Set",
        placeholder="Type part of the Ad Set name...",
    ).strip()

if campaign_search and not filtered.empty:
    filtered = filtered[
        filtered["campaign_name"].fillna("").str.contains(
            campaign_search,
            case=False,
            regex=False,
        )
    ].copy()

if adset_search and not filtered.empty:
    filtered = filtered[
        filtered["adset_name"].fillna("").str.contains(
            adset_search,
            case=False,
            regex=False,
        )
    ].copy()

metric_1, metric_2, metric_3 = st.columns(3)
metric_1.metric(
    "Filtered Ad Accounts",
    int(filtered["account_id"].nunique()) if not filtered.empty else 0,
)
metric_2.metric(
    "Filtered Campaigns",
    int(filtered["campaign_id"].nunique()) if not filtered.empty else 0,
)
metric_3.metric("Filtered Ad Sets", int(len(filtered)))

st.divider()
st.subheader("Ad Set Age Targeting")

# Exactly the requested output columns.
display_df = filtered.rename(
    columns={
        "account_name": "Ad Account Name",
        "campaign_name": "Campaign Name",
        "adset_name": "Ad Set Name",
        "min_age": "Min Age",
        "max_age": "Max Age",
    }
)[
    [
        "Ad Account Name",
        "Campaign Name",
        "Ad Set Name",
        "Min Age",
        "Max Age",
    ]
].reset_index(drop=True)

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    height=650,
)

csv_data = display_df.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "Download filtered CSV",
    data=csv_data,
    file_name="meta_adset_min_max_age.csv",
    mime="text/csv",
    use_container_width=True,
    disabled=display_df.empty,
)

st.info(
    "Min Age and Max Age are the current targeting settings returned by Meta for each Ad Set. "
    "They are not historical metrics, so this lightweight version intentionally has no Today / Yesterday / date-range selector."
)

if show_account_sources:
    with st.expander("Loaded Ad Account Sources", expanded=False):
        if raw_accounts_df.empty:
            st.info("No source details in the saved snapshot.")
        else:
            st.dataframe(raw_accounts_df, use_container_width=True, hide_index=True)

errors = meta.get("errors", [])
if errors:
    with st.expander(f"Errors ({len(errors)})", expanded=False):
        st.code("\n".join(map(str, errors)))
