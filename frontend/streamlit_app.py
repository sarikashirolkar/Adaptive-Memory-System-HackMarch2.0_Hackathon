import streamlit as st
import requests
import plotly.graph_objects as go
import math
import os
import time
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
API_BASE    = os.getenv("BACKEND_URL", "http://localhost:8000")
N8N_BASE    = os.getenv("N8N_URL",     "http://localhost:5678")
REFRESH_MS  = 5000   # auto-refresh interval

st.set_page_config(
    page_title="Adaptive Memory System",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Auto-refresh ─────────────────────────────────────────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=REFRESH_MS, key="dashboard_refresh")
except ImportError:
    pass   # works without it; user just refreshes manually

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* Dark card style */
  .metric-card {
    background: #1a1d27;
    border: 1px solid #2e3250;
    border-radius: 12px;
    padding: 20px 24px;
    text-align: center;
  }
  .metric-val  { font-size: 2.2rem; font-weight: 800; line-height: 1; }
  .metric-lbl  { font-size: 0.78rem; color: #7986cb; margin-top: 6px;
                 text-transform: uppercase; letter-spacing: 0.8px; }

  /* Review badge */
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 700;
    margin: 2px;
  }
  .badge-ok       { background:#1b4332; color:#48cfad; }
  .badge-fail     { background:#4a1515; color:#ff6b6b; }
  .badge-active   { background:#4a3f00; color:#feca57; }
  .badge-pending  { background:#1e2235; color:#7986cb; }

  /* Lesson card */
  .lesson-card {
    background: #1a1d27;
    border: 1px solid #2e3250;
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 12px;
  }
  .lesson-title { font-size: 1rem; font-weight: 700; color: #e8eaf6; }
  .lesson-meta  { font-size: 0.78rem; color: #7986cb; margin-top: 4px; }

  /* Progress segment row */
  .seg-row { display:flex; gap:4px; margin-top:10px; }
  .seg {
    flex:1; height:8px; border-radius:4px;
    background: #22263a; border: 1px solid #2e3250;
  }
  .seg-ok     { background: #48cfad; border-color: #48cfad; }
  .seg-fail   { background: #ff6b6b; border-color: #ff6b6b; }
  .seg-active { background: #feca57; border-color: #feca57;
                animation: pulse 1s infinite; }
  @keyframes pulse { 50% { opacity: 0.5; } }

  /* Upcoming reminder row */
  .reminder-row {
    background: #1a1d27;
    border-left: 3px solid #6c63ff;
    border-radius: 0 8px 8px 0;
    padding: 10px 14px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .reminder-row.overdue { border-left-color: #ff6b6b; }
  .reminder-row.soon    { border-left-color: #feca57; }
  .time-chip {
    font-size: 0.8rem; font-weight: 700;
    padding: 4px 12px; border-radius: 20px;
    background: #22263a; color: #e8eaf6;
  }
  .time-chip.now     { color: #feca57; }
  .time-chip.overdue { color: #ff6b6b; }

  /* Hide default streamlit chrome */
  #MainMenu { visibility: hidden; }
  footer     { visibility: hidden; }
  .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ── API helpers ───────────────────────────────────────────────────────────────
def api_get(path: str):
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=4)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def api_post(path: str, payload: dict, use_n8n: bool = False):
    base = N8N_BASE if use_n8n else API_BASE
    try:
        r = requests.post(f"{base}{path}", json=payload, timeout=6)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return None

def api_delete(path: str):
    try:
        r = requests.delete(f"{API_BASE}{path}", timeout=4)
        return r.ok
    except Exception:
        return False

def format_countdown(seconds: float) -> tuple[str, str]:
    """Returns (label, css_class)"""
    if seconds < 0:
        return "OVERDUE", "overdue"
    elif seconds < 60:
        return f"{int(seconds)}s", "now"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s", ""
    else:
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        return f"{h}h {m}m", ""

def seg_class(status: str) -> str:
    return {"remembered":"seg-ok","forgot":"seg-fail","sent":"seg-active"}.get(status,"")

def badge_class(status: str) -> str:
    return {"remembered":"badge-ok","forgot":"badge-fail","sent":"badge-active"}.get(status,"badge-pending")


# ── Forgetting curve plot ─────────────────────────────────────────────────────
def build_curve_fig(demo_mode: bool = True):
    label = "minutes" if demo_mode else "days"
    max_t = 30
    review_pts = [1, 3, 7, 14, 30]  # demo minutes / real days

    def decay(t, S=4):
        return 100 * math.exp(-t / S)

    def spaced(t):
        stability, last, retention = 4.0, 0, 100.0
        for rp in review_pts:
            if t < rp:
                return min(100, retention * math.exp(-(t - last) / stability))
            elapsed = rp - last
            retention = min(100, retention * math.exp(-elapsed / stability) * 1.8 + 10)
            stability *= 2.2
            last = rp
        return min(100, retention * math.exp(-(t - last) / stability))

    ts   = [i * max_t / 300 for i in range(301)]
    ys1  = [decay(t) for t in ts]
    ys2  = [spaced(t) for t in ts]

    fig = go.Figure()

    # Shaded area between curves
    fig.add_trace(go.Scatter(
        x=ts + ts[::-1], y=ys2 + ys1[::-1],
        fill='toself', fillcolor='rgba(72,207,173,0.07)',
        line=dict(color='rgba(0,0,0,0)'), showlegend=False, hoverinfo='skip'
    ))

    # Without revision
    fig.add_trace(go.Scatter(
        x=ts, y=ys1, name="Without revision",
        line=dict(color="#6c63ff", width=2.5),
        hovertemplate=f"%{{x:.1f}} {label}: %{{y:.0f}}%<extra></extra>"
    ))

    # With spaced repetition
    fig.add_trace(go.Scatter(
        x=ts, y=ys2, name="With spaced repetition",
        line=dict(color="#48cfad", width=2.5),
        hovertemplate=f"%{{x:.1f}} {label}: %{{y:.0f}}%<extra></extra>"
    ))

    # Review markers
    for i, rp in enumerate(review_pts):
        y_val = spaced(rp)
        fig.add_vline(x=rp, line=dict(color="#feca57", width=1, dash="dot"))
        fig.add_trace(go.Scatter(
            x=[rp], y=[y_val], name=f"Review #{i+1}",
            mode="markers+text",
            marker=dict(color="#feca57", size=10, symbol="circle"),
            text=[f"R{i+1}"], textposition="top center",
            textfont=dict(color="#feca57", size=11),
            hovertemplate=f"Review #{i+1} @ {rp} {label}<extra></extra>"
        ))

    fig.update_layout(
        paper_bgcolor="#1a1d27",
        plot_bgcolor="#1a1d27",
        font=dict(color="#e8eaf6"),
        legend=dict(
            bgcolor="#22263a", bordercolor="#2e3250",
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0
        ),
        margin=dict(l=40, r=20, t=40, b=40),
        xaxis=dict(
            title=f"Time ({label})",
            gridcolor="#2e3250", zerolinecolor="#2e3250",
            tickfont=dict(color="#7986cb")
        ),
        yaxis=dict(
            title="Retention %",
            range=[0, 105],
            gridcolor="#2e3250", zerolinecolor="#2e3250",
            tickfont=dict(color="#7986cb"),
            ticksuffix="%"
        ),
        height=320,
        hovermode="x unified"
    )
    return fig


# ════════════════════════════════════════════════════════════════
#  MAIN APP
# ════════════════════════════════════════════════════════════════

# Header
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.markdown("## 🧠 Adaptive Memory System")
    st.caption("Ebbinghaus Forgetting Curve · HackMarch 2.0 · Certisured")
with col_h2:
    stats = api_get("/api/stats") or {}
    demo_on = stats.get("demo_mode", True)
    st.markdown(
        f"<div style='text-align:right;padding-top:12px'>"
        f"<span style='background:#feca57;color:#111;font-size:0.7rem;"
        f"font-weight:700;padding:4px 10px;border-radius:20px;'>"
        f"{'⚡ DEMO MODE' if demo_on else '🌍 REAL MODE'}</span></div>",
        unsafe_allow_html=True
    )

st.divider()

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_dash, tab_add, tab_lessons = st.tabs(["📊 Dashboard", "📖 Add Lesson", "🗂️ All Lessons"])


# ════════ TAB 1: DASHBOARD ════════════════════════════════════
with tab_dash:

    # Stats row
    total    = stats.get("total_lessons", 0)
    pending  = stats.get("reminders_pending", 0)
    rem_ok   = stats.get("reminders_remembered", 0)
    rem_fail = stats.get("reminders_forgot", 0)
    ret_rate = stats.get("retention_rate", 0)
    nxt_secs = stats.get("next_review_in_seconds")

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, val, lbl, color in [
        (c1, total,              "Total Lessons",    "#6c63ff"),
        (c2, pending,            "Pending Reviews",  "#feca57"),
        (c3, rem_ok,             "Remembered",       "#48cfad"),
        (c4, rem_fail,           "Forgot",           "#ff6b6b"),
        (c5, f"{int(ret_rate*100)}%", "Retention",   "#e8eaf6"),
    ]:
        col.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-val' style='color:{color}'>{val}</div>"
            f"<div class='metric-lbl'>{lbl}</div>"
            f"</div>",
            unsafe_allow_html=True
        )

    # Next review countdown
    if nxt_secs is not None:
        label, _ = format_countdown(nxt_secs)
        st.info(f"⏱️ Next review fires in **{label}**", icon=None)

    st.markdown("### 📉 Ebbinghaus Forgetting Curve")
    st.plotly_chart(build_curve_fig(demo_on), use_container_width=True)

    # Upcoming reminders
    st.markdown("### ⏰ Upcoming Reviews")
    upcoming = api_get("/api/reminders/upcoming?limit=15") or []
    if not upcoming:
        st.markdown(
            "<div style='text-align:center;color:#7986cb;padding:24px'>No reminders scheduled yet</div>",
            unsafe_allow_html=True
        )
    else:
        for r in upcoming:
            secs = r.get("seconds_until_due", 0)
            overdue = r.get("is_overdue", False)
            label, chip_cls = format_countdown(-1 if overdue else secs)
            row_cls = "overdue" if overdue else ("soon" if secs < 120 else "")
            st.markdown(
                f"<div class='reminder-row {row_cls}'>"
                f"<div>"
                f"<span style='font-weight:700;color:#e8eaf6'>{r['lesson_title']}</span>"
                f"<span style='color:#7986cb;font-size:0.78rem'> · Review #{r['review_number']} ({r.get('interval_label','')})</span>"
                f"</div>"
                f"<span class='time-chip {chip_cls}'>{label}</span>"
                f"</div>",
                unsafe_allow_html=True
            )


# ════════ TAB 2: ADD LESSON ════════════════════════════════════
with tab_add:
    st.markdown("### 📖 Log a New Lesson")

    col_form, col_info = st.columns([3, 2])

    with col_form:
        with st.form("add_lesson_form", clear_on_submit=True):
            title   = st.text_input("Topic / Lesson Name",
                                    placeholder="e.g. Neural Networks, Photosynthesis, World War 2")
            content = st.text_area("Key Points (optional)",
                                   placeholder="Brief description or bullet points...",
                                   height=100)
            demo    = st.toggle("Demo Mode (compress days → minutes)", value=True)

            # Show interval preview
            intervals_demo = {1:"1 min",2:"3 min",3:"7 min",4:"14 min",5:"30 min"}
            intervals_real = {1:"1 day",2:"3 days",3:"7 days",4:"14 days",5:"30 days"}
            iv = intervals_demo if demo else intervals_real

            st.markdown("**Scheduled reviews:**")
            iv_cols = st.columns(5)
            for i, col in enumerate(iv_cols, 1):
                col.markdown(
                    f"<div style='text-align:center;background:#22263a;border-radius:8px;padding:8px 4px'>"
                    f"<div style='color:#6c63ff;font-weight:700'>#{i}</div>"
                    f"<div style='color:#7986cb;font-size:0.75rem'>{iv[i]}</div>"
                    f"</div>", unsafe_allow_html=True
                )

            submitted = st.form_submit_button("➕ Log This Lesson", use_container_width=True, type="primary")

        if submitted:
            if not title.strip():
                st.error("Please enter a lesson topic.")
            else:
                with st.spinner("Logging lesson and scheduling reviews..."):
                    # Send through n8n webhook if available, else direct to backend
                    result = api_post(
                        "/webhook/new-lesson",
                        {"title": title.strip(), "content": content.strip(), "demo_mode": demo},
                        use_n8n=True
                    )
                    if result is None:
                        # Fallback: direct to backend
                        result = api_post(
                            "/api/lessons",
                            {"title": title.strip(), "content": content.strip(), "demo_mode": demo}
                        )

                if result:
                    st.success(f"✅ **\"{title}\"** logged successfully!")
                    reminders = result.get("reminders", [])
                    if reminders:
                        st.markdown("**Reviews scheduled at:**")
                        for r in reminders:
                            scheduled = datetime.fromisoformat(r["scheduled_at"])
                            local_time = scheduled.strftime("%H:%M:%S")
                            st.markdown(
                                f"- Review **#{r['review_number']}** ({r.get('interval_label','')}) → `{local_time} UTC`"
                            )
                else:
                    st.error("Failed to add lesson. Make sure the backend is running.")

    with col_info:
        st.markdown("### 💡 How It Works")
        st.markdown("""
The **Ebbinghaus Forgetting Curve** shows memory decays exponentially over time.

**Without review:** You forget ~70% within a day.

**With spaced repetition:** Each review at the right moment resets retention and increases memory stability.

**The Formula:**
```
R(t) = e^(-t/S)
```
- **R** = retention (0–100%)
- **t** = time since last review
- **S** = stability (doubles each review)

**Demo mode** compresses real intervals so judges can see reminders fire live:

| Review | Real | Demo |
|--------|------|------|
| #1 | 1 day | 1 min |
| #2 | 3 days | 3 min |
| #3 | 7 days | 7 min |
| #4 | 14 days | 14 min |
| #5 | 30 days | 30 min |
        """)


# ════════ TAB 3: ALL LESSONS ════════════════════════════════════
with tab_lessons:
    st.markdown("### 🗂️ All Lessons")

    lessons = api_get("/api/lessons") or []

    if not lessons:
        st.markdown(
            "<div style='text-align:center;color:#7986cb;padding:48px'>"
            "<div style='font-size:3rem'>🌱</div>"
            "<div>Add your first lesson to get started</div>"
            "</div>",
            unsafe_allow_html=True
        )
    else:
        col_a, col_b = st.columns([1, 1])
        for idx, lesson in enumerate(lessons):
            col = col_a if idx % 2 == 0 else col_b
            with col:
                reminders = lesson.get("reminders", [])
                completed = lesson.get("reminders_completed", 0)
                total_r   = lesson.get("reminders_total", 5)
                created   = lesson.get("created_at", "")

                # Progress segments HTML
                segs = ""
                for n in range(1, 6):
                    r = next((x for x in reminders if x["review_number"] == n), None)
                    cls = seg_class(r["status"]) if r else ""
                    title_tip = f"Review #{n}: {r['status']}" if r else f"Review #{n}: not scheduled"
                    segs += f"<div class='seg {cls}' title='{title_tip}'></div>"

                # Review badges
                badges = ""
                for r in reminders:
                    bcls = badge_class(r["status"])
                    badges += f"<span class='badge {bcls}'>#{r['review_number']} {r['status']}</span>"

                # Next review info
                next_rev = lesson.get("next_review_at")
                next_num = lesson.get("next_review_number")
                if next_rev:
                    dt = datetime.fromisoformat(next_rev)
                    secs_left = (dt.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                    cdown, _ = format_countdown(secs_left)
                    next_str = f"Next: Review #{next_num} in {cdown}"
                else:
                    next_str = "✅ All reviews complete!"

                st.markdown(
                    f"<div class='lesson-card'>"
                    f"<div class='lesson-title'>📚 {lesson['title']}</div>"
                    f"<div class='lesson-meta'>"
                    f"{lesson.get('content','')[:80] + '…' if len(lesson.get('content','')) > 80 else lesson.get('content','')}"
                    f"</div>"
                    f"<div class='seg-row'>{segs}</div>"
                    f"<div style='margin-top:8px'>{badges}</div>"
                    f"<div style='font-size:0.75rem;color:#7986cb;margin-top:8px'>"
                    f"{completed}/{total_r} reviews done · {next_str}"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )

                # Delete button
                if st.button(f"🗑 Delete", key=f"del_{lesson['id']}", help=f"Delete \"{lesson['title']}\""):
                    if api_delete(f"/api/lessons/{lesson['id']}"):
                        st.rerun()
