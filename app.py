import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gspread
from google.oauth2.service_account import Credentials
import anthropic
import os
import json
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Paid Media Budget Planning",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #00A1E0;
        margin-bottom: 0.25rem;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #f8f9ff 0%, #e8f4fd 100%);
        border-radius: 12px;
        padding: 1.25rem;
        border-left: 4px solid #00A1E0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }
    .metric-label {
        font-size: 0.8rem;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.25rem;
    }
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #1a1a2e;
    }
    .metric-delta-pos { color: #28a745; font-size: 0.85rem; }
    .metric-delta-neg { color: #dc3545; font-size: 0.85rem; }
    .chat-message-user {
        background: #00A1E0;
        color: white;
        padding: 0.75rem 1rem;
        border-radius: 12px 12px 2px 12px;
        margin: 0.5rem 0;
        max-width: 80%;
        margin-left: auto;
    }
    .chat-message-ai {
        background: #f0f4f8;
        color: #1a1a2e;
        padding: 0.75rem 1rem;
        border-radius: 12px 12px 12px 2px;
        margin: 0.5rem 0;
        max-width: 85%;
    }
    .stSelectbox > div > div { border-radius: 8px; }
    div[data-testid="stSidebar"] { background: #f8f9ff; }
</style>
""", unsafe_allow_html=True)


# ── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data():
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "")
    spreadsheet_id = os.getenv(
        "SPREADSHEET_ID", "1OWWNDzFTSZSK2bKoFfCn9oSlkgvRUQxWFohPC5pOkg0"
    )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    # Support both file path and Streamlit secrets
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=scopes
        )
    elif creds_path and os.path.exists(creds_path):
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    else:
        st.error("Google credentials not found. Check your .env or Streamlit secrets.")
        st.stop()

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.get_worksheet(0)
    raw = ws.get_all_values()

    # Row 2 (index 1) is the header
    header = raw[1]
    rows = [r for r in raw[2:] if any(c.strip() for c in r)]

    df = pd.DataFrame(rows, columns=header)
    df = df[df["OU"].str.strip() != ""]

    # Clean currency columns
    currency_cols = [c for c in df.columns if any(
        fy in c for fy in ["FY27", "FY28", "FY26", "FY25"]
    )]
    for col in currency_cols:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[$,]", "", regex=True)
                .str.strip()
            )
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df, currency_cols


def format_currency(value):
    if value >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    elif value >= 1_000:
        return f"${value/1_000:.0f}K"
    return f"${value:,.0f}"


def delta_html(current, prior):
    if prior == 0:
        return ""
    pct = ((current - prior) / prior) * 100
    arrow = "▲" if pct >= 0 else "▼"
    cls = "metric-delta-pos" if pct >= 0 else "metric-delta-neg"
    return f'<span class="{cls}">{arrow} {abs(pct):.1f}% vs prior year</span>'


# ── App ───────────────────────────────────────────────────────────────────────

try:
    df, currency_cols = load_data()
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Determine available year columns
fy_full_cols = [c for c in df.columns if c in ["FY28", "FY27", "FY26", "FY25"]]
fy_order = ["FY28", "FY27", "FY26", "FY25"]
fy_full_cols = [c for c in fy_order if c in fy_full_cols]

# ── Sidebar filters ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Filters")

    all_ous = sorted(df["OU"].dropna().unique())
    selected_ous = st.multiselect("Operating Unit (OU)", all_ous, default=all_ous)

    all_clouds = sorted(df["Cloud"].dropna().unique())
    selected_clouds = st.multiselect("Cloud", all_clouds, default=all_clouds)

    all_buckets = sorted(df["Bucket"].dropna().unique())
    selected_buckets = st.multiselect("Bucket", all_buckets, default=all_buckets)

    st.markdown("---")
    primary_year = st.selectbox(
        "Primary Year", fy_full_cols, index=0 if fy_full_cols else 0
    )
    compare_year = st.selectbox(
        "Compare Against",
        [c for c in fy_full_cols if c != primary_year],
        index=0 if len(fy_full_cols) > 1 else 0,
    )

filtered = df[
    df["OU"].isin(selected_ous)
    & df["Cloud"].isin(selected_clouds)
    & df["Bucket"].isin(selected_buckets)
].copy()

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown('<div class="main-header">Paid Media Budget Planning</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Media Finance · FY Budget Analysis & AI Assistant</div>', unsafe_allow_html=True)

# ── KPI row ──────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)

primary_total = filtered[primary_year].sum() if primary_year in filtered.columns else 0
compare_total = filtered[compare_year].sum() if compare_year in filtered.columns else 0
yoy_diff = primary_total - compare_total
yoy_pct = ((yoy_diff / compare_total) * 100) if compare_total > 0 else 0

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{primary_year} Total Budget</div>
        <div class="metric-value">{format_currency(primary_total)}</div>
        {delta_html(primary_total, compare_total)}
    </div>""", unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{compare_year} Actual</div>
        <div class="metric-value">{format_currency(compare_total)}</div>
    </div>""", unsafe_allow_html=True)

with col3:
    arrow = "▲" if yoy_diff >= 0 else "▼"
    cls = "metric-delta-pos" if yoy_diff >= 0 else "metric-delta-neg"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">YoY Change</div>
        <div class="metric-value">{format_currency(abs(yoy_diff))}</div>
        <span class="{cls}">{arrow} {abs(yoy_pct):.1f}%</span>
    </div>""", unsafe_allow_html=True)

with col4:
    num_ous = filtered["OU"].nunique()
    num_clouds = filtered["Cloud"].nunique()
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Scope</div>
        <div class="metric-value">{num_ous} OUs</div>
        <span style="color:#888;font-size:0.85rem">{num_clouds} Clouds selected</span>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Charts ───────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["By OU", "By Cloud", "By Bucket"])

with tab1:
    ou_data = (
        filtered.groupby("OU")[[primary_year, compare_year]]
        .sum()
        .reset_index()
        .sort_values(primary_year, ascending=False)
    ) if primary_year in filtered.columns and compare_year in filtered.columns else pd.DataFrame()

    if not ou_data.empty:
        fig = go.Figure()
        fig.add_bar(
            x=ou_data["OU"], y=ou_data[compare_year],
            name=compare_year, marker_color="#c8e6f5"
        )
        fig.add_bar(
            x=ou_data["OU"], y=ou_data[primary_year],
            name=primary_year, marker_color="#00A1E0"
        )
        fig.update_layout(
            barmode="group", height=420,
            plot_bgcolor="white", paper_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=40, b=60),
            xaxis=dict(tickangle=-35),
            yaxis=dict(tickprefix="$", tickformat=",.0f"),
        )
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    if primary_year in filtered.columns and compare_year in filtered.columns:
        cloud_data = (
            filtered.groupby("Cloud")[[primary_year, compare_year]]
            .sum()
            .reset_index()
            .sort_values(primary_year, ascending=False)
        )
        fig2 = go.Figure()
        fig2.add_bar(
            x=cloud_data["Cloud"], y=cloud_data[compare_year],
            name=compare_year, marker_color="#c8e6f5"
        )
        fig2.add_bar(
            x=cloud_data["Cloud"], y=cloud_data[primary_year],
            name=primary_year, marker_color="#00A1E0"
        )
        fig2.update_layout(
            barmode="group", height=420,
            plot_bgcolor="white", paper_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=40, b=60),
            xaxis=dict(tickangle=-35),
            yaxis=dict(tickprefix="$", tickformat=",.0f"),
        )
        st.plotly_chart(fig2, use_container_width=True)

with tab3:
    if primary_year in filtered.columns and compare_year in filtered.columns:
        bucket_data = (
            filtered.groupby("Bucket")[[primary_year, compare_year]]
            .sum()
            .reset_index()
            .sort_values(primary_year, ascending=False)
        )
        fig3 = px.pie(
            bucket_data, values=primary_year, names="Bucket",
            color_discrete_sequence=px.colors.sequential.Blues_r,
            hole=0.4,
        )
        fig3.update_layout(height=420, margin=dict(t=40))
        st.plotly_chart(fig3, use_container_width=True)

st.markdown("---")

# ── Data table ───────────────────────────────────────────────────────────────
with st.expander("View raw data table"):
    display_cols = ["OU", "APM Level", "Cloud", "Bucket"] + [
        c for c in fy_full_cols if c in filtered.columns
    ]
    st.dataframe(
        filtered[display_cols].sort_values(["OU", "Cloud"]).reset_index(drop=True),
        use_container_width=True,
        height=350,
    )

st.markdown("---")

# ── AI Chat ──────────────────────────────────────────────────────────────────
st.markdown("### Ask a Budget Question")
st.markdown(
    "Ask anything about the data — totals by OU, YoY comparisons, Cloud breakdowns, scenario questions."
)

api_key = os.getenv("ANTHROPIC_API_KEY", "")
if hasattr(st, "secrets") and "ANTHROPIC_API_KEY" in st.secrets:
    api_key = st.secrets["ANTHROPIC_API_KEY"]

if not api_key:
    st.warning(
        "Anthropic API key not set. Add ANTHROPIC_API_KEY to your .env file to enable the AI assistant."
    )
else:
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Display chat history
    for msg in st.session_state.chat_history:
        if msg["role"] == "user":
            st.markdown(
                f'<div class="chat-message-user">{msg["content"]}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="chat-message-ai">{msg["content"]}</div>',
                unsafe_allow_html=True,
            )

    with st.form("chat_form", clear_on_submit=True):
        user_input = st.text_input(
            "Your question",
            placeholder="e.g. How much was spent in UKI for Marketing Cloud in FY27?",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Ask", use_container_width=True)

    if submitted and user_input.strip():
        # Build data summary for context
        summary_rows = []
        for _, row in filtered.iterrows():
            parts = [f"OU={row['OU']}", f"Cloud={row['Cloud']}", f"Bucket={row['Bucket']}"]
            for fy in fy_full_cols:
                if fy in row:
                    parts.append(f"{fy}=${row[fy]:,.0f}")
            summary_rows.append(", ".join(parts))
        data_context = "\n".join(summary_rows[:800])  # cap at 800 rows

        system_prompt = f"""You are a budget analyst assistant for Salesforce's Media Finance team.
You have access to the Paid Media budget dataset below.
Answer questions clearly and concisely. Format numbers with $ and commas.
When doing YoY comparisons, show both values and the % change.
If asked about data not present, say so clearly.

DATA:
{data_context}"""

        st.session_state.chat_history.append({"role": "user", "content": user_input})

        with st.spinner("Thinking..."):
            try:
                client = anthropic.Anthropic(api_key=api_key)
                messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.chat_history
                ]
                response = client.messages.create(
                    model="claude-sonnet-5",
                    max_tokens=1024,
                    system=system_prompt,
                    messages=messages,
                )
                answer = response.content[0].text
                st.session_state.chat_history.append(
                    {"role": "assistant", "content": answer}
                )
                st.rerun()
            except Exception as e:
                st.error(f"AI error: {e}")

    if st.session_state.get("chat_history"):
        if st.button("Clear chat", type="secondary"):
            st.session_state.chat_history = []
            st.rerun()
