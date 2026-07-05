#!/usr/bin/env python3
"""
delta_bot.py — Discord bot that extracts Delta license keys from Platorelay URLs.

Commands:
  /key <url>    — Extract a Delta key from a Platorelay link
  /history      — Show keys extracted this session
  /stats        — Bot stats (uptime, keys found, success rate)
  /help         — Show all commands
  /ping         — Check if bot is alive

Setup:
  pip install discord.py requests pycryptodome pillow numpy
  Set DISCORD_TOKEN in .env or environment variable
  python3 delta_bot.py
"""

import os
import sys
import json
import time
import base64
import asyncio
import urllib.parse
import datetime
import traceback
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import discord
from discord.ext import commands
from discord import app_commands

import requests
import numpy as np
from PIL import Image

try:
    from Crypto.Cipher import AES
    from Crypto.Util import Counter as CryptoCounter
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ── Config ─────────────────────────────────────────────────────────────────────

TOKEN = os.environ.get("DISCORD_TOKEN", "")
PREFIX = "!"  # fallback prefix commands (!key, !help, etc.)
ALLOWED_ROLES: list[str] = []  # restrict to role names e.g. ["Admin", "VIP"], empty = everyone

# ── Captcha / API constants ────────────────────────────────────────────────────

CAPTCHA_BASE = "https://captcha.platorelay.com"
API_BASE     = "https://auth.platorelay.com/api"
BG           = np.array([236, 238, 243, 255])

# ── Session state ──────────────────────────────────────────────────────────────

key_history: list[dict] = []
bot_start_time          = time.time()
total_attempts          = 0
total_successes         = 0
total_failures          = 0

executor = ThreadPoolExecutor(max_workers=4)


# ── HTTP session factory (one per extraction thread) ───────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Accept":  "application/json, text/plain, */*",
        "Origin":  "https://auth.platorelay.com",
        "Referer": "https://auth.platorelay.com/",
    })
    return s


# ── Crypto helpers ─────────────────────────────────────────────────────────────

def build_meta(ticket: str) -> str:
    if not HAS_CRYPTO:
        return "empty"
    key = bytes([ord(c) for c in ticket[0:16]])
    iv  = int.from_bytes(bytes([ord(c) for c in ticket[16:32]]), "big")
    pt  = json.dumps({"browserInfo": []}, separators=(",", ":")).encode()
    ctr = CryptoCounter.new(128, initial_value=iv)
    return AES.new(key, AES.MODE_CTR, counter=ctr).encrypt(pt).hex()


# ── Numpy helpers ──────────────────────────────────────────────────────────────

def _center_of_mass(mask):
    indices = np.argwhere(mask)
    if len(indices) == 0:
        return (0.0, 0.0)
    return tuple(indices.mean(axis=0))


# ── Captcha solvers ────────────────────────────────────────────────────────────

def _load_frames(image_path: str, session: requests.Session) -> list:
    resp = session.get(f"{CAPTCHA_BASE}{image_path}", timeout=20)
    img  = Image.open(BytesIO(resp.content))
    frames = []
    try:
        while True:
            frames.append(np.array(img.copy().convert("RGBA")))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    return frames


def _solve_coherence(frames) -> tuple:
    grays = [np.array(Image.fromarray(f).convert("L")).astype(float) for f in frames]
    H, W  = grays[0].shape
    PATCH, STRIDE = 50, 12
    scores, positions = [], []
    f0, f1 = grays[0], grays[1]
    for cy in range(PATCH // 2, H - PATCH // 2, STRIDE):
        for cx in range(PATCH // 2, W - PATCH // 2, STRIDE):
            y0, x0 = cy - PATCH // 2, cx - PATCH // 2
            p0 = f0[y0:y0+PATCH, x0:x0+PATCH]
            p1 = f1[y0:y0+PATCH, x0:x0+PATCH]
            F0, F1 = np.fft.fft2(p0), np.fft.fft2(p1)
            denom = np.abs(F0 * np.conj(F1)) + 1e-10
            R = F0 * np.conj(F1) / denom
            r = np.abs(np.fft.ifft2(R))
            scores.append(r.max() / (r.mean() + 1e-10))
            positions.append((cx, cy))
    top5 = np.array(positions)[np.argsort(scores)[::-1][:5]]
    return float(top5[:, 0].mean()), float(top5[:, 1].mean())


def _find_centroid(frame, rgb, tol=40):
    diff    = np.abs(frame[:, :, :3].astype(int) - np.array(rgb)).sum(axis=2)
    bg_diff = np.abs(frame.astype(int) - BG.astype(int)).sum(axis=2)
    mask    = (diff < tol) & (bg_diff > 20)
    if mask.sum() < 5:
        return None
    cy, cx = _center_of_mass(mask)
    return cx, cy


def _solve_driftodd(frames) -> tuple:
    f0    = frames[0]
    diff0 = np.abs(f0.astype(int) - BG.astype(int)).sum(axis=2)
    fg_ys, fg_xs = np.where(diff0 > 30)
    if len(fg_ys) == 0:
        h, w = f0.shape[:2]
        return w / 2.0, h / 2.0

    color_buckets: dict = {}
    for y, x in zip(fg_ys, fg_xs):
        raw = f0[y, x, :3]
        key = tuple((raw // 30).tolist())
        if key not in color_buckets:
            color_buckets[key] = {"xs": [], "ys": [], "raw": raw.tolist()}
        color_buckets[key]["xs"].append(int(x))
        color_buckets[key]["ys"].append(int(y))

    blobs = []
    for key, bucket in color_buckets.items():
        n = len(bucket["xs"])
        if n < 30:
            continue
        cx0 = float(np.mean(bucket["xs"]))
        cy0 = float(np.mean(bucket["ys"]))
        blobs.append((n, cx0, cy0, bucket["raw"]))
    blobs.sort(reverse=True)

    results = []
    for area, cx0, cy0, color in blobs[:10]:
        positions = [_find_centroid(f, color) for f in frames]
        positions = [p for p in positions if p is not None]
        if len(positions) < len(frames) * 0.5:
            continue
        xs_t = [p[0] for p in positions]
        ys_t = [p[1] for p in positions]
        cx_m = float(np.mean(xs_t))
        cy_m = float(np.mean(ys_t))
        angles = [np.arctan2(p[1] - cy_m, p[0] - cx_m) for p in positions]
        diffs  = []
        for j in range(1, len(angles)):
            da = angles[j] - angles[j - 1]
            if da >  np.pi: da -= 2 * np.pi
            if da < -np.pi: da += 2 * np.pi
            diffs.append(da)
        omega = float(np.mean(diffs)) if diffs else 0.0
        results.append({"area": area, "cx0": cx0, "cy0": cy0, "omega": omega})

    cw  = [s for s in results if s["omega"] < -0.05]
    ccw = [s for s in results if s["omega"] >  0.05]
    odd = (ccw if len(ccw) < len(cw) else cw) or ccw or cw or results
    if not odd:
        h, w = frames[0].shape[:2]
        return w / 2.0, h / 2.0
    best = max(odd, key=lambda s: s["area"])
    return float(best["cx0"]), float(best["cy0"])


def solve_captcha(session: requests.Session, max_retries: int = 8) -> str:
    for attempt in range(1, max_retries + 1):
        ch     = session.get(f"{CAPTCHA_BASE}/api/challenge", timeout=20).json()
        ctype  = ch.get("type", "")
        frames = _load_frames(ch["image"], session)

        if ctype == "coherence":
            x, y = _solve_coherence(frames)
        elif ctype == "driftodd":
            x, y = _solve_driftodd(frames)
        else:
            h, w = frames[0].shape[:2]
            x, y = w / 2.0, h / 2.0

        resp = session.post(
            f"{CAPTCHA_BASE}/api/answer",
            json={"challenge_id": ch["challenge_id"], "x": x, "y": y},
            timeout=20,
        ).json()

        if resp.get("success"):
            return resp["token"]
        if attempt < max_retries:
            time.sleep(1)

    raise RuntimeError("ShapeCaptcha: max retries exceeded")


# ── URL / ticket helpers ───────────────────────────────────────────────────────

def ticket_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    qs     = urllib.parse.parse_qs(parsed.query)
    ticket = (qs.get("d") or qs.get("ticket") or [""])[0]
    return ticket or url.strip()


def next_ticket_from_ad_url(ad_url: str) -> str | None:
    parsed = urllib.parse.urlparse(ad_url)
    qs     = urllib.parse.parse_qs(parsed.query)
    r_enc  = qs.get("r", [""])[0]
    if not r_enc:
        return None
    try:
        inner = base64.b64decode(urllib.parse.unquote(r_enc)).decode()
        t = ticket_from_url(inner)
        return t or None
    except Exception:
        return None


# ── Core extraction (blocking — run in executor) ───────────────────────────────

def extract_key(url: str, log: list[str]) -> str:
    """Blocking function — runs in thread executor. Returns key string or raises."""
    session  = make_session()
    ticket   = ticket_from_url(url)

    def process(ticket: str, depth: int = 0) -> str | None:
        log.append(f"📡 Connecting… (step {depth})")
        meta_resp = session.get(
            f"{API_BASE}/session/metadata",
            params={"ticket": ticket},
            timeout=20,
        ).json()

        if not meta_resp.get("success"):
            raise RuntimeError(f"Metadata failed: {meta_resp}")

        data     = meta_resp.get("data", {})
        profile  = data.get("activeRevenueProfile", {})
        n_checks = profile.get("checkpointCount", 1)
        service  = str(profile.get("service", 3))

        # Already done?
        status = session.get(f"{API_BASE}/session/status", params={"ticket": ticket}, timeout=20).json()
        if isinstance(status.get("data"), dict):
            k = status["data"].get("key", "")
            if k and k != "KEY_NOT_FOUND":
                return k

        enc_meta = build_meta(ticket)

        for chk in range(n_checks):
            log.append(f"🧩 Solving CAPTCHA ({chk + 1}/{n_checks})…")
            if depth > 0 or chk > 0:
                time.sleep(8)

            token = solve_captcha(session)
            log.append(f"✅ CAPTCHA solved!")

            step = session.put(
                f"{API_BASE}/session/step",
                params={"ticket": ticket, "service": service},
                json={"captcha": token, "meta": enc_meta, "stream": None, "resolved": False},
                timeout=20,
            ).json()

            if "too fast" in step.get("message", "").lower():
                log.append("⏳ Rate limited — waiting 30s…")
                time.sleep(30)
                token = solve_captcha(session)
                step = session.put(
                    f"{API_BASE}/session/step",
                    params={"ticket": ticket, "service": service},
                    json={"captcha": token, "meta": enc_meta, "stream": None, "resolved": False},
                    timeout=20,
                ).json()

            if step.get("success") and isinstance(step.get("data"), dict):
                ad_url      = step["data"].get("url", "")
                next_ticket = next_ticket_from_ad_url(ad_url)
                if next_ticket and depth < 10:
                    result = process(next_ticket, depth + 1)
                    if result:
                        return result

        log.append("🔍 Polling for key…")
        for _ in range(15):
            status = session.get(f"{API_BASE}/session/status", params={"ticket": ticket}, timeout=20).json()
            if isinstance(status.get("data"), dict):
                k = status["data"].get("key", "")
                if k and k != "KEY_NOT_FOUND":
                    return k
            time.sleep(3)

        return None

    result = process(ticket)
    if not result:
        raise RuntimeError("Key not returned — session may have expired.")
    return result


# ── Discord bot setup ──────────────────────────────────────────────────────────

intents         = discord.Intents.default()
intents.message_content = True
bot             = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
tree            = bot.tree


def check_roles(interaction: discord.Interaction) -> bool:
    if not ALLOWED_ROLES:
        return True
    user_roles = [r.name for r in interaction.user.roles]
    return any(r in user_roles for r in ALLOWED_ROLES)


# ── /key ──────────────────────────────────────────────────────────────────────

@tree.command(name="key", description="Extract a Delta key from a Platorelay URL")
@app_commands.describe(url="Your Delta/Platorelay link (https://auth.platorelay.com/a?d=...)")
async def slash_key(interaction: discord.Interaction, url: str):
    global total_attempts, total_successes, total_failures

    if not check_roles(interaction):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    total_attempts += 1
    start = time.time()
    log: list[str] = []

    embed = discord.Embed(
        title="🔑 Delta Key Extractor",
        description="Starting extraction…",
        color=discord.Color.blue(),
    )
    embed.set_footer(text="This may take 20–60 seconds")
    await interaction.response.send_message(embed=embed)

    async def update_embed(desc: str, color=discord.Color.blue()):
        e = discord.Embed(title="🔑 Delta Key Extractor", description=desc, color=color)
        e.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.edit_original_response(embed=e)

    try:
        loop = asyncio.get_event_loop()

        # Run blocking extraction in thread
        key = await loop.run_in_executor(executor, extract_key, url, log)

        elapsed = round(time.time() - start, 1)
        total_successes += 1

        key_history.append({
            "key":     key,
            "user":    str(interaction.user),
            "time":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": elapsed,
        })

        e = discord.Embed(title="✅ Key Extracted!", color=discord.Color.green())
        e.add_field(name="🔑 Delta Key", value=f"```{key}```", inline=False)
        e.add_field(name="⏱️ Time", value=f"{elapsed}s", inline=True)
        e.add_field(name="👤 Requested by", value=interaction.user.mention, inline=True)
        e.set_footer(text=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        await interaction.edit_original_response(embed=e)

    except Exception as exc:
        total_failures += 1
        elapsed = round(time.time() - start, 1)
        err     = str(exc)[:300]
        e = discord.Embed(title="❌ Extraction Failed", description=f"```{err}```", color=discord.Color.red())
        e.add_field(name="⏱️ Time", value=f"{elapsed}s", inline=True)
        e.add_field(name="💡 Tip", value="Make sure the URL is fresh — tickets expire quickly!", inline=False)
        await interaction.edit_original_response(embed=e)
        traceback.print_exc()


# Prefix fallback: !key <url>
@bot.command(name="key")
async def prefix_key(ctx, *, url: str = ""):
    if not url:
        await ctx.reply("Usage: `!key <platorelay_url>`")
        return
    # Re-use slash logic via fake interaction wrapper — just call core directly
    global total_attempts, total_successes, total_failures
    total_attempts += 1
    start = time.time()
    log: list[str] = []

    msg = await ctx.reply("🔄 Extracting key…")
    try:
        loop = asyncio.get_event_loop()
        key  = await loop.run_in_executor(executor, extract_key, url, log)
        elapsed = round(time.time() - start, 1)
        total_successes += 1
        key_history.append({
            "key":     key,
            "user":    str(ctx.author),
            "time":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": elapsed,
        })
        e = discord.Embed(title="✅ Key Extracted!", color=discord.Color.green())
        e.add_field(name="🔑 Delta Key", value=f"```{key}```", inline=False)
        e.add_field(name="⏱️ Time", value=f"{elapsed}s", inline=True)
        await msg.edit(content=None, embed=e)
    except Exception as exc:
        total_failures += 1
        err = str(exc)[:300]
        e   = discord.Embed(title="❌ Failed", description=f"```{err}```", color=discord.Color.red())
        e.add_field(name="💡 Tip", value="Get a fresh URL from Delta and try again.", inline=False)
        await msg.edit(content=None, embed=e)


# ── /history ──────────────────────────────────────────────────────────────────

@tree.command(name="history", description="Show all keys extracted this session")
async def slash_history(interaction: discord.Interaction):
    if not key_history:
        await interaction.response.send_message("📭 No keys extracted yet this session.", ephemeral=True)
        return
    e = discord.Embed(title=f"📜 Key History ({len(key_history)} total)", color=discord.Color.blurple())
    for i, entry in enumerate(key_history[-10:], 1):
        elapsed_str = f"{entry['elapsed']:.1f}s"
        e.add_field(
            name=f"#{i} — {entry['time']}",
            value=f"```{entry['key']}```by {entry['user']} • {elapsed_str}",
            inline=False,
        )
    if len(key_history) > 10:
        e.set_footer(text=f"Showing last 10 of {len(key_history)}")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ── /stats ────────────────────────────────────────────────────────────────────

@tree.command(name="stats", description="Show bot stats")
async def slash_stats(interaction: discord.Interaction):
    uptime = str(datetime.timedelta(seconds=int(time.time() - bot_start_time)))
    rate   = f"{(total_successes / total_attempts * 100):.0f}%" if total_attempts else "N/A"
    e = discord.Embed(title="📊 Delta Bot Stats", color=discord.Color.gold())
    e.add_field(name="⏱️ Uptime",       value=uptime,            inline=True)
    e.add_field(name="🔑 Keys Found",   value=str(total_successes), inline=True)
    e.add_field(name="❌ Failures",     value=str(total_failures),  inline=True)
    e.add_field(name="📨 Total Runs",   value=str(total_attempts),  inline=True)
    e.add_field(name="🎯 Success Rate", value=rate,               inline=True)
    await interaction.response.send_message(embed=e)


# ── /ping ─────────────────────────────────────────────────────────────────────

@tree.command(name="ping", description="Check if the bot is alive")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! `{latency}ms`")


# ── /help ─────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    e = discord.Embed(
        title="🤖 Delta Key Bot — Commands",
        description="Auto-extracts Delta license keys from Platorelay URLs.",
        color=discord.Color.blurple(),
    )
    e.add_field(name="/key <url>",  value="Extract a Delta key from a link",   inline=False)
    e.add_field(name="/history",    value="Show keys extracted this session",   inline=False)
    e.add_field(name="/stats",      value="Bot uptime, success rate, totals",   inline=False)
    e.add_field(name="/ping",       value="Check bot latency",                  inline=False)
    e.add_field(name="/help",       value="Show this message",                  inline=False)
    e.add_field(
        name="📌 Notes",
        value=(
            "• Slash commands (`/key`) are preferred\n"
            "• Prefix commands also work: `!key <url>`\n"
            "• Keys expire fast — always use a fresh URL\n"
            "• Extraction takes ~20–60 seconds"
        ),
        inline=False,
    )
    e.set_footer(text="Powered by platorelay.com key system")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ── Bot events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Delta Bot online as {bot.user} ({bot.user.id})")
    print(f"   Slash commands synced. Use /key <url> to extract keys.")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="/key — Delta Key Extractor",
        )
    )


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: Set DISCORD_TOKEN environment variable or paste it into this script.")
        print("  export DISCORD_TOKEN='your-bot-token-here'")
        sys.exit(1)
    bot.run(TOKEN)
