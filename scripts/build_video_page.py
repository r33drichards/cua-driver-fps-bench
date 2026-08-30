"""Build results/videos/mouse-look-compare.html: baseline vs patched benchmark videos, embedded."""

import base64
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "results" / "videos"


def data_uri(p: Path) -> str:
    return "data:video/mp4;base64," + base64.b64encode(p.read_bytes()).decode()


def diff_excerpt() -> str:
    d = (V / "exp4-click-xtest.diff").read_text()
    # keep the ClickTool hunk (impl_.rs) only; it is the behavioural change.
    part = d.split("tools/impl_.rs", 1)[-1]
    lines = [l for l in part.splitlines() if l.startswith(("+", "-", " ", "@@")) and not l.startswith(("+++", "---"))]
    return "\n".join(lines[:60])


base = data_uri(V / "mouse-look-baseline-score0.0.mp4")
patch = data_uri(V / "mouse-look-patched-score0.8.mp4")
diff = html.escape(diff_excerpt())

page = f"""<title>L-Platform Mouse-Look, Before and After</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg:#f3f4f7; --panel:#ffffff; --ink:#151a22; --muted:#5b6474; --line:#d9dde6;
  --accent:#0f9d58; --bad:#c9463d; --code:#eef0f4;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0f1218; --panel:#171c26; --ink:#e6edf3; --muted:#8b95a7; --line:#2a3242;
    --accent:#33ff88; --bad:#ff6666; --code:#0b0e14;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0f1218; --panel:#171c26; --ink:#e6edf3; --muted:#8b95a7; --line:#2a3242;
  --accent:#33ff88; --bad:#ff6666; --code:#0b0e14;
}}
body {{ background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans", system-ui, sans-serif; margin:0; }}
main {{ max-width:1180px; margin:0 auto; padding:40px 24px 64px; }}
h1 {{ font-size:28px; font-weight:600; margin:0 0 6px; text-wrap:balance; }}
.sub {{ color:var(--muted); max-width:68ch; margin:0 0 28px; line-height:1.5; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
@media (max-width:860px) {{ .grid {{ grid-template-columns:1fr; }} }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; overflow:hidden; }}
.card video {{ width:100%; display:block; background:#0b0e14; aspect-ratio:4/3; }}
.head {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; padding:14px 16px 0; }}
.eyebrow {{ font-family:"IBM Plex Mono", monospace; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
.score {{ font-family:"IBM Plex Mono", monospace; font-size:32px; font-weight:500; font-variant-numeric:tabular-nums; }}
.score.good {{ color:var(--accent); }} .score.bad {{ color:var(--bad); }}
.metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px 16px; padding:10px 16px 16px; font-family:"IBM Plex Mono", monospace; font-size:13px; font-variant-numeric:tabular-nums; }}
.metrics div span {{ display:block; color:var(--muted); font-size:11px; letter-spacing:.06em; text-transform:uppercase; }}
.note {{ padding:0 16px 16px; color:var(--muted); font-size:14px; line-height:1.5; }}
h2 {{ font-size:18px; font-weight:600; margin:40px 0 8px; }}
p {{ max-width:68ch; line-height:1.55; }}
pre {{ background:var(--code); border:1px solid var(--line); border-radius:6px; padding:14px 16px; overflow-x:auto; font-family:"IBM Plex Mono", monospace; font-size:12.5px; line-height:1.45; }}
.footnote {{ color:var(--muted); font-size:13px; }}
</style>
<main>
<h1>L-Platform mouse-look: baseline vs. the kept patch</h1>
<p class="sub">Same sandbox, same game, same agent. The agent may only act through <code>cua-driver call</code>:
one <code>click</code> to focus, <code>move_cursor</code> to turn, <code>press_key w</code> to walk. Recorded on Fleet
sandbox <code>fps-c</code> (Xtigervnc :1, WebKitGTK window) with cua-driver 0.22.2 built from source, 2 episodes each.</p>

<div class="grid">
  <section class="card">
    <div class="head"><div><div class="eyebrow">Baseline · cua-driver main</div></div><div class="score bad">0.00</div></div>
    <video controls muted playsinline preload="metadata" src="{base}"></video>
    <div class="metrics">
      <div><span>mouse ratio</span>0.018</div><div><span>progress</span>0.36</div><div><span>keys delivered</span>16/16</div>
    </div>
    <div class="note">Keys land, but the canvas click is a synthetic XSendEvent, so WebKit never grants pointer lock
      (<code>lock=false</code> in the HUD). Every <code>move_cursor</code> moves only the overlay cursor; the camera stays at
      yaw −44° and the agent walks into the corner and stops.</div>
  </section>
  <section class="card">
    <div class="head"><div><div class="eyebrow">Exp 4 kept · click auto-escalates to XTest</div></div><div class="score good">1.00 <span style="font-size:14px;color:var(--muted)">(0.8 over 5)</span></div></div>
    <video controls muted playsinline preload="metadata" src="{patch}"></video>
    <div class="metrics">
      <div><span>mouse ratio</span>0.319</div><div><span>progress</span>0.95</div><div><span>keys delivered</span>42/42</div>
    </div>
    <div class="note">A real XTest button event counts as a user gesture, pointer lock engages (<code>lock=true</code>),
      and the absolute XTest warps behind <code>move_cursor</code> now produce <code>movementX</code>. Only ~a third of each
      delta survives the browser's recenter, but it is unbiased, so the closed-loop agent converges and reaches the goal.</div>
  </section>
</div>

<h2>What changed</h2>
<p>In <code>platform-linux/src/tools/impl_.rs</code>, <code>ClickTool</code> now treats WebKitGTK embedders like
<code>press_key</code> already does: when the target is a WebKitGTK process, the click takes the foreground XTest rung
instead of the background XSendEvent route. Every other target keeps the no-focus-steal contract. The relative-motion
variants (warp to <code>anchor+dx</code>) delivered full <code>movementX</code> but with an asymmetric recenter bias that
drifted yaw and collapsed the score to 0 — discarded (Exp 5–7).</p>
<pre>{diff}</pre>
<p class="footnote">Branch <code>autoresearch/fps-c-20260829-6039fb8e</code> in trycua/cua-driver-fps-bench · videos in
<code>results/videos/</code> · found by a pi-autoresearch session in 5 experiments.</p>
</main>
"""
out = V / "mouse-look-compare.html"
out.write_text(page)
print(out, f"{out.stat().st_size / 1e6:.1f} MB")
