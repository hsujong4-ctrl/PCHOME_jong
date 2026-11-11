import base64
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="PCHome 商品價格分析", layout="wide")
st.title("PCHome 商品搜尋與價格分析")

BASE_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
FONT_PATH = Path("fonts/TaipeiSansTCBeta-Regular.ttf")
FONT_URL = "https://drive.google.com/uc?id=1eGAsTN1HBpJAkeVM57_C7ccp7hbgSz3_&export=download"
FONT_FAMILY = "Taipei Sans TC Beta"
CHART_FONT = f"{FONT_FAMILY}, Microsoft JhengHei, PingFang TC, Noto Sans TC, sans-serif"


# ---------- 中文字型（取代 !wget） ----------
@st.cache_resource(show_spinner="載入中文字型...")
def load_font_b64() -> str | None:
    """下載字型到 fonts/ 資料夾（若已存在則直接讀取），回傳 base64 字串。"""
    if not FONT_PATH.exists():
        try:
            FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
            resp = requests.get(FONT_URL, timeout=60)
            resp.raise_for_status()
            # Google Drive 大檔可能回傳 HTML 確認頁，這時不能當字型使用
            if b"<html" in resp.content[:200].lower():
                return None
            FONT_PATH.write_bytes(resp.content)
        except requests.RequestException:
            return None
    return base64.b64encode(FONT_PATH.read_bytes()).decode()


# ---------- 爬取 PCHome ----------
def scrape_pchome(keyword: str, pages: int, delay: float) -> pd.DataFrame:
    frames = []
    progress = st.progress(0.0, text="準備爬取...")
    for i in range(1, pages + 1):
        url = f"{BASE_URL}?q={quote(keyword)}&page={i}&sort=sale/dc"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        prods = resp.json().get("prods", [])
        if prods:
            frames.append(pd.DataFrame(prods))
        progress.progress(i / pages, text=f"已完成第 {i}/{pages} 頁")
        if i < pages and delay > 0:
            time.sleep(delay)  # 每頁間隔，避免請求過於頻繁
    progress.empty()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------- 側邊欄 ----------
with st.sidebar:
    st.header("爬取設定")
    delay = st.slider(
        "每頁間隔秒數", min_value=0, max_value=10, value=10,
        help="原程式每頁等待 10 秒，建議保留以降低被封鎖的風險",
    )

# ---------- 搜尋表單 ----------
with st.form("search_form"):
    keyword = st.text_input("產品名稱", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=50, value=1, step=1)
    submitted = st.form_submit_button("開始爬取")

if submitted:
    if not keyword.strip():
        st.warning("請輸入產品名稱")
    else:
        try:
            df_raw = scrape_pchome(keyword.strip(), int(pages), delay)
            st.session_state["raw"] = df_raw
            st.session_state["keyword"] = keyword.strip()
        except requests.RequestException as e:
            st.error(f"爬取失敗：{e}")

# ---------- 字型注入（讓 plotly 圖表使用中文字型） ----------
font_b64 = load_font_b64()
if font_b64:
    st.markdown(
        f"<style>@font-face {{font-family: '{FONT_FAMILY}';"
        f"src: url(data:font/ttf;base64,{font_b64}) format('truetype');}}</style>",
        unsafe_allow_html=True,
    )

# ---------- 顯示結果 ----------
df_raw = st.session_state.get("raw")
if df_raw is None:
    st.info("請輸入產品名稱與頁數，然後按「開始爬取」。")
    st.stop()

if df_raw.empty:
    st.warning("查無資料，請換個關鍵字試試。")
    st.stop()

if not {"name", "price"} <= set(df_raw.columns):
    st.error("回傳資料缺少 name 或 price 欄位，請確認 API 格式是否變動。")
    st.stop()

keyword_shown = st.session_state.get("keyword", "")
st.subheader(f"「{keyword_shown}」搜尋結果")

# 與原程式相同：只保留 name、price，並去除空值
data = df_raw[["name", "price"]].dropna().copy()
data["price"] = pd.to_numeric(data["price"], errors="coerce")
data = data.dropna(subset=["price"]).reset_index(drop=True)
data["rank"] = data.index + 1

# ---------- 摘要 ----------
c1, c2, c3, c4 = st.columns(4)
c1.metric("商品筆數", f"{len(data):,}")
c2.metric("平均價格", f"{data['price'].mean():,.0f}")
c3.metric("最高價格", f"{data['price'].max():,.0f}")
c4.metric("最低價格", f"{data['price'].min():,.0f}")

# ---------- 折線圖 ----------
st.markdown("### 價格折線圖（依銷量排序）")
if len(data) > 1:
    top_n = st.slider("顯示前 N 筆", min_value=1, max_value=len(data),
                      value=min(70, len(data)))
else:
    top_n = len(data)
line_df = data.head(top_n)

fig_line = px.line(
    line_df, x="rank", y="price", markers=True,
    labels={"rank": "排名（依銷量）", "price": "售價"},
    title=f"{keyword_shown} 售價走勢（前 {top_n} 筆）",
)
fig_line.add_hline(
    y=data["price"].mean(), line_dash="dash", line_color="red",
    annotation_text=f"平均 {data['price'].mean():,.0f}",
)
fig_line.update_layout(font_family=CHART_FONT, height=450)
st.plotly_chart(fig_line, use_container_width=True)

# ---------- 價格區間（圓餅圖與旭日圖共用） ----------
lo, hi = data["price"].min(), data["price"].max()
if lo == hi:
    hi = lo + 1
edges = np.linspace(lo, hi, 6)
labels = [f"{edges[i]:,.0f}–{edges[i + 1]:,.0f}" for i in range(5)]
data["價格區間"] = pd.cut(
    data["price"], bins=edges, labels=labels, include_lowest=True
).astype(str)
data["短名稱"] = data["name"].astype(str).str.slice(0, 15)

col_pie, col_sun = st.columns(2)

with col_pie:
    st.markdown("### 價格區間分布（圓餅圖）")
    counts = data["價格區間"].value_counts().reset_index()
    counts.columns = ["價格區間", "數量"]
    fig_pie = px.pie(
        counts, names="價格區間", values="數量",
        title="各價格區間商品數量",
    )
    fig_pie.update_traces(textinfo="percent+label")
    fig_pie.update_layout(font_family=CHART_FONT, height=500)
    st.plotly_chart(fig_pie, use_container_width=True)

with col_sun:
    st.markdown("### 價格區間與商品（旭日圖）")
    fig_sun = px.sunburst(
        data, path=["價格區間", "短名稱"],
        title="價格區間 → 商品名稱",
    )
    fig_sun.update_layout(font_family=CHART_FONT, height=500)
    st.plotly_chart(fig_sun, use_container_width=True)

# ---------- 資料表與下載 ----------
st.markdown("### 資料表")
st.dataframe(data[["rank", "name", "price", "價格區間"]], height=400)

st.download_button(
    label="下載完整 CSV（取代原程式的 to_csv）",
    data=df_raw.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"{keyword_shown}_PCHOME.csv",
    mime="text/csv",
)
