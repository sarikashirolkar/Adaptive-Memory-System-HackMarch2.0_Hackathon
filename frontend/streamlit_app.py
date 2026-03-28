import streamlit as st
import requests
import plotly.graph_objects as go
import math
import os
import time
from datetime import datetime, timezone
import google.generativeai as genai
import json
import re
import seaborn as sns
import matplotlib.pyplot as plt

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
    if time.time() >= st.session_state.get("suspend_autorefresh_until", 0):
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


def get_gemini_model_name(api_key: str) -> str:
    """
    Resolve a supported Gemini model dynamically.
    Priority:
    1) GEMINI_MODEL env override
    2) Preferred flash models available to this key
    3) First model that supports generateContent
    """
    override = os.getenv("GEMINI_MODEL", "").strip()
    if override:
        return override

    cache_key = "_resolved_gemini_model"
    cached = st.session_state.get(cache_key)
    if cached:
        return cached

    genai.configure(api_key=api_key)
    models = list(genai.list_models())
    supported = [
        m.name for m in models
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    ]
    if not supported:
        raise RuntimeError("No Gemini models with generateContent are available for this API key.")

    preferred = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
    ]

    for pref in preferred:
        for name in supported:
            if name.endswith("/" + pref) or name == pref:
                st.session_state[cache_key] = name
                return name

    st.session_state[cache_key] = supported[0]
    return supported[0]


def parse_json_payload(raw: str) -> dict:
    """Extract and parse JSON payload from Gemini text responses."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty response from model.")

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    return json.loads(text)


def normalize_quiz_data(payload: dict) -> dict:
    """Normalize question schema so UI logic doesn't break on slight model drift."""
    questions = payload.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("Model did not return any questions.")

    normalized = []
    labels = ["A", "B", "C", "D"]
    for q in questions[:5]:
        question = str(q.get("question", "")).strip()
        options = q.get("options", [])
        if not question or not isinstance(options, list) or len(options) < 4:
            continue

        clean_options = [str(opt).strip() for opt in options[:4]]
        lettered_options = []
        for idx, opt in enumerate(clean_options):
            if re.match(r"^[A-Da-d][\.\):]\s*", opt):
                lettered_options.append(opt)
            else:
                lettered_options.append(f"{labels[idx]}. {opt}")

        correct_raw = str(q.get("correct", "")).strip()
        correct_match = re.match(r"([A-Da-d])", correct_raw)
        if correct_match:
            correct = correct_match.group(1).upper()
        else:
            correct = next(
                (labels[i] for i, opt in enumerate(lettered_options) if correct_raw.lower() in opt.lower()),
                "A",
            )

        normalized.append({
            "question": question,
            "options": lettered_options,
            "correct": correct,
        })

    if len(normalized) < 3:
        raise ValueError("Could not parse enough valid questions from model output.")
    return {"questions": normalized}

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
tab_dash, tab_add, tab_lessons, tab_stats, tab_quiz, tab_chat = st.tabs([
    "📊 Dashboard", "📖 Add Lesson", "🗂️ All Lessons", "📈 Stats", "📝 Quiz", "🤖 Chatbot"
])


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


# ════════ TAB 4: STATS (HEATMAP) ════════════════════════════════
with tab_stats:
    st.markdown("### 📈 Lesson Analytics")
    st.caption("Choose a lesson to view revision depth, quiz participation, completion left, and overall progress quality.")

    lessons_data = api_get("/api/lessons") or []
    if not lessons_data:
        st.markdown(
            "<div style='text-align:center;color:#7986cb;padding:48px'>"
            "<div style='font-size:3rem'>📭</div>"
            "<div>No lessons yet — add one to see analytics</div>"
            "</div>",
            unsafe_allow_html=True
        )
    else:
        lesson_titles = [l["title"] for l in lessons_data]
        selected_stats_lesson = st.selectbox(
            "Select lesson for analytics",
            lesson_titles,
            key="stats_lesson_select",
        )
        selected_meta = next(l for l in lessons_data if l["title"] == selected_stats_lesson)
        analytics = api_get(f"/api/lessons/{selected_meta['id']}/analytics")

        if not analytics:
            st.error("Unable to load lesson analytics right now.")
        else:
            done = analytics.get("review_stages_completed", 0)
            left = analytics.get("review_stages_remaining", 0)
            quizzes_attended = analytics.get("quizzes_attended", 0)
            revision_attempts = analytics.get("reminder_attempts_total", 0)
            retention_rate = int((analytics.get("retention_rate", 0) or 0) * 100)
            retry_count = analytics.get("retry_count", 0)
            completion_pct = int((analytics.get("completion_pct", 0) or 0) * 100)

            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Done", f"{done}/5")
            m2.metric("Left", left)
            m3.metric("Quizzes Attended", quizzes_attended)
            m4.metric("Revision Attempts", revision_attempts)
            m5.metric("Recall Stability", f"{retention_rate}%")
            m6.metric("Retries", retry_count)

            st.progress(max(0.0, min(1.0, completion_pct / 100)), text=f"Completion progress: {completion_pct}%")

            stage_details = analytics.get("stage_details", [])
            x_labels = [f"R{d['review_number']}" for d in stage_details]
            attempts = [d["attempts"] for d in stage_details]
            states = ["Completed" if d["completed"] else "Pending" for d in stage_details]

            sns.set_theme(style="darkgrid")
            fig, ax = plt.subplots(figsize=(8, 3.8))
            sns.barplot(x=x_labels, y=attempts, hue=states, palette={"Completed": "#48cfad", "Pending": "#feca57"}, ax=ax)
            ax.set_title("Revision Attempts by Review Stage")
            ax.set_xlabel("Review Stage")
            ax.set_ylabel("Attempts")
            ax.legend(title="Stage State")
            st.pyplot(fig, clear_figure=True, use_container_width=True)

            c_quiz, c_status = st.columns(2)
            with c_quiz:
                st.markdown("**Quiz Activity**")
                diff = analytics.get("quiz_difficulty_breakdown", {})
                st.write(
                    f"- Easy quizzes: {diff.get('easy', 0)}\n"
                    f"- Medium quizzes: {diff.get('medium', 0)}\n"
                    f"- Hard quizzes: {diff.get('hard', 0)}"
                )
            with c_status:
                st.markdown("**Revision Status**")
                status = analytics.get("reminder_attempt_status_counts", {})
                st.write(
                    f"- Remembered attempts: {status.get('remembered', 0)}\n"
                    f"- Forgot attempts: {status.get('forgot', 0)}\n"
                    f"- Pending attempts: {status.get('pending', 0)}\n"
                    f"- Sent (awaiting response): {status.get('sent', 0)}"
                )

            st.markdown("### 🧾 Overall Review")
            st.info(analytics.get("overall_review", "No overall review available yet."))


# ════════ TAB 5: QUIZ ═══════════════════════════════════════════
with tab_quiz:
    st.markdown("### 📝 Quiz Yourself")
    st.caption("Test your knowledge — your revision schedule adjusts automatically based on your score.")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    if not GEMINI_API_KEY:
        st.error("GEMINI_API_KEY not set. Add it to your .env file and restart Docker.")
    else:
        quiz_lessons = api_get("/api/lessons") or []

        if not quiz_lessons:
            st.info("Add a lesson first to take a quiz.")
        else:
            # Pre-select lesson from URL param (set when clicking quiz button in Telegram)
            url_lesson_id = st.query_params.get("lesson_id", "")
            lesson_names  = [l["title"] for l in quiz_lessons]
            default_idx   = 0
            if url_lesson_id:
                for i, l in enumerate(quiz_lessons):
                    if str(l["id"]) == str(url_lesson_id):
                        default_idx = i
                        break

            col_sel, col_diff = st.columns([2, 1])
            with col_sel:
                selected_title = st.selectbox("Select Lesson", lesson_names, index=default_idx, key="quiz_lesson_sel")
            with col_diff:
                difficulty = st.radio("Difficulty", ["Easy", "Medium", "Hard"], horizontal=True, key="quiz_diff")

            selected_lesson = next(l for l in quiz_lessons if l["title"] == selected_title)

            # Clear quiz state if lesson or difficulty changed
            prev_key = st.session_state.get("quiz_prev_key", "")
            curr_key = f"{selected_lesson['id']}_{difficulty}"
            if prev_key != curr_key:
                for k in ["quiz_questions", "quiz_answers", "quiz_submitted",
                          "quiz_score", "quiz_correct", "quiz_total", "quiz_result"]:
                    st.session_state.pop(k, None)
                st.session_state["quiz_prev_key"] = curr_key

            if not st.session_state.get("quiz_submitted"):
                if st.button("🎯 Generate Quiz", type="primary", key="gen_quiz_btn"):
                    st.session_state["suspend_autorefresh_until"] = time.time() + 45
                    with st.spinner("Generating questions..."):
                        try:
                            genai.configure(api_key=GEMINI_API_KEY)
                            model_name = get_gemini_model_name(GEMINI_API_KEY)
                            model = genai.GenerativeModel(
                                model_name=model_name,
                                generation_config={"response_mime_type": "application/json"},
                            )
                            prompt = f"""Generate a {difficulty.lower()} quiz with 5 multiple choice questions about: "{selected_lesson['title']}".
{"Context: " + selected_lesson["content"] if selected_lesson.get("content") else ""}

Difficulty guide:
- Easy: basic recall and definitions
- Medium: understanding and application
- Hard: analysis, edge cases, deep understanding

Return valid JSON only, exactly in this format:
{{
  "questions": [
    {{
      "question": "Question text here?",
      "options": ["A. First option", "B. Second option", "C. Third option", "D. Fourth option"],
      "correct": "A"
    }}
  ]
}}"""
                            response = model.generate_content(prompt)
                            raw = (getattr(response, "text", "") or "").strip()
                            if not raw and getattr(response, "candidates", None):
                                parts = response.candidates[0].content.parts
                                raw = "\n".join(
                                    getattr(p, "text", "") for p in parts if getattr(p, "text", "")
                                ).strip()

                            quiz_data = normalize_quiz_data(parse_json_payload(raw))
                            st.session_state["quiz_questions"]  = quiz_data["questions"]
                            st.session_state["quiz_lesson_id"]  = selected_lesson["id"]
                            st.session_state["quiz_difficulty"] = difficulty.lower()
                            st.session_state["quiz_answers"]    = {}
                        except Exception as e:
                            st.error(f"Failed to generate quiz. Details: {e}")

                # Display questions
                if st.session_state.get("quiz_questions"):
                    st.divider()
                    questions = st.session_state["quiz_questions"]
                    for i, q in enumerate(questions):
                        st.markdown(f"**Q{i+1}. {q['question']}**")
                        st.radio(
                            f"q_{i}",
                            q["options"],
                            key=f"quiz_q_{i}",
                            label_visibility="collapsed",
                        )

                    if st.button("📊 Submit Answers", type="primary", key="submit_quiz_btn"):
                        questions = st.session_state["quiz_questions"]
                        correct_count = 0
                        for i, q in enumerate(questions):
                            selected_opt = st.session_state.get(f"quiz_q_{i}", "")
                            # selected_opt is like "A. option text", correct is "A"
                            if selected_opt and selected_opt[0].upper() == q["correct"].strip()[0].upper():
                                correct_count += 1

                        total = len(questions)
                        score = int((correct_count / total) * 100)

                        result = api_post(
                            f"/api/lessons/{st.session_state['quiz_lesson_id']}/quiz/submit",
                            {
                                "difficulty":         st.session_state["quiz_difficulty"],
                                "score":              score,
                                "questions_total":    total,
                                "questions_correct":  correct_count,
                            },
                        )

                        st.session_state["quiz_submitted"] = True
                        st.session_state["quiz_score"]     = score
                        st.session_state["quiz_correct"]   = correct_count
                        st.session_state["quiz_total"]     = total
                        st.session_state["quiz_result"]    = result or {}
                        st.rerun()

            # Results screen
            if st.session_state.get("quiz_submitted"):
                score  = st.session_state["quiz_score"]
                correct = st.session_state["quiz_correct"]
                total   = st.session_state["quiz_total"]
                result  = st.session_state.get("quiz_result", {})

                color = "#48cfad" if score >= 70 else "#feca57" if score >= 40 else "#ff6b6b"
                grade = "Excellent! 🎉" if score >= 90 else "Good 👍" if score >= 70 else "Keep going 💪" if score >= 40 else "Needs work 📚"

                st.markdown(
                    f"<div style='text-align:center;padding:32px;background:#1a1d27;"
                    f"border-radius:12px;border:1px solid #2e3250'>"
                    f"<div style='font-size:3.5rem;font-weight:800;color:{color}'>{score}%</div>"
                    f"<div style='color:#e8eaf6;font-size:1.1rem;margin-top:4px'>{grade}</div>"
                    f"<div style='color:#7986cb;margin-top:6px'>{correct} / {total} correct</div>"
                    f"<div style='color:#e8eaf6;margin-top:16px;font-size:0.95rem'>"
                    f"{result.get('message', '')}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                st.markdown("")
                if st.button("🔄 Take Another Quiz", key="retry_quiz_btn"):
                    for k in ["quiz_questions", "quiz_answers", "quiz_submitted", "quiz_score",
                              "quiz_correct", "quiz_total", "quiz_result", "quiz_prev_key"]:
                        st.session_state.pop(k, None)
                    st.rerun()


# ════════ TAB 6: CHATBOT ════════════════════════════════════════
with tab_chat:
    st.markdown("### 🤖 Study Assistant")
    st.caption("Ask anything about your lessons or learning topics. Select a lesson to give the AI context.")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    if not GEMINI_API_KEY:
        st.error("GEMINI_API_KEY not set. Add it to your .env file and restart Docker.")
    else:
        # Lesson context selector
        chat_lessons = api_get("/api/lessons") or []
        lesson_options = ["None (general chat)"] + [l["title"] for l in chat_lessons]
        selected_lesson_title = st.selectbox("Lesson context:", lesson_options, key="chat_lesson_select")

        selected_lesson = None
        if selected_lesson_title != "None (general chat)":
            selected_lesson = next((l for l in chat_lessons if l["title"] == selected_lesson_title), None)

        # Build system prompt
        system_prompt = (
            "You are a helpful study assistant for a spaced repetition learning app. "
            "Help the user understand and remember their study topics. "
            "Be concise, clear, and educational. Use examples where helpful."
        )
        if selected_lesson:
            system_prompt += (
                f"\n\nThe user is currently studying: '{selected_lesson['title']}'."
            )
            if selected_lesson.get("content"):
                system_prompt += f"\nLesson notes: {selected_lesson['content']}"
            system_prompt += (
                "\nFocus your answers around this topic. "
                "Help them understand it deeply so they remember it long-term."
            )

        # Chat history in session state
        if "chat_messages" not in st.session_state:
            st.session_state.chat_messages = []

        # Clear chat button
        if st.button("🗑 Clear chat", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()

        # Display chat history
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # Chat input
        user_input = st.chat_input("Ask something about your lesson...")

        if user_input:
            st.session_state.chat_messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.markdown(user_input)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        genai.configure(api_key=GEMINI_API_KEY)
                        model_name = get_gemini_model_name(GEMINI_API_KEY)
                        model = genai.GenerativeModel(
                            model_name=model_name,
                            system_instruction=system_prompt,
                        )
                        history = [
                            {"role": m["role"], "parts": [m["content"]]}
                            for m in st.session_state.chat_messages[:-1]
                        ]
                        chat = model.start_chat(history=history)
                        response = chat.send_message(user_input)
                        reply = response.text
                    except Exception as e:
                        reply = f"Error: {str(e)}"

                st.markdown(reply)
                st.session_state.chat_messages.append({"role": "assistant", "content": reply})
