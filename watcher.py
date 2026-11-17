import os, re, json, sys, tempfile, pathlib, subprocess
from datetime import datetime, timezone, timedelta
import requests

E = lambda k, d="": os.environ.get(k) or d
YT_KEY, SB, SBK = E("YOUTUBE_API_KEY"), E("SUPABASE_URL").rstrip("/"), E("SUPABASE_SERVICE_ROLE_KEY")
BUCKET = E("SUPABASE_BUCKET", "clips")
PER_STREAM, CLIP_LEN = int(E("CLIPS_PER_STREAM", "3")), int(E("CLIP_SECONDS", "45"))
MAX_HOURS, LOOKBACK = float(E("MAX_STREAM_HOURS", "4")), int(E("LOOKBACK_DAYS", "3"))
PRIVACY = E("YT_PRIVACY", "private")
API = "https://www.googleapis.com/youtube/v3"
H = {"apikey": SBK, "Authorization": f"Bearer {SBK}", "Content-Type": "application/json"}
YTDLP = ["yt-dlp", "--no-warnings", "--js-runtimes", "node"]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr or "command failed")[-300:])


def yt(path, **p):
    r = requests.get(f"{API}/{path}", params={**p, "key": YT_KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


def db(method, path, **kw):
    r = requests.request(method, f"{SB}/rest/v1/{path}", headers={**H, "Prefer": "return=representation"}, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.text else []


def fail(row, why):
    print("FAILED:", why)
    db("PATCH", "clips", params={"id": f"eq.{row['id']}"}, json={"storage_path": "failed: " + why[:200]})
    return True


def vid_of(url):
    m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([\w-]{11})", url)
    return m.group(1) if m else None


def iso_seconds(d):
    g = [int(x or 0) for x in re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d).groups()]
    return g[0] * 86400 + g[1] * 3600 + g[2] * 60 + g[3]


def discover():
    for line in pathlib.Path("channels.txt").read_text().splitlines():
        h = line.split("#")[0].strip()
        if not h:
            continue
        try:
            up = yt("channels", part="contentDetails", forHandle=h)["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            ids = [i["contentDetails"]["videoId"] for i in yt("playlistItems", part="contentDetails", playlistId=up, maxResults=10)["items"]]
            for v in yt("videos", part="snippet,liveStreamingDetails", id=",".join(ids))["items"]:
                if "liveStreamingDetails" not in v:
                    continue
                state, end = v["snippet"]["liveBroadcastContent"], v["liveStreamingDetails"].get("actualEndTime")
                print(h, "|", v["snippet"]["title"][:50], "|", state)
                if state != "none" or not end:
                    continue  # still live: wait until it ends
                if datetime.fromisoformat(end.replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(days=LOOKBACK):
                    continue
                url = f"https://www.youtube.com/watch?v={v['id']}"
                if not db("GET", "clips", params={"source_url": f"eq.{url}", "select": "id"}):
                    db("POST", "clips", json={"title": v["snippet"]["title"], "source_url": url})
        except Exception as e:
            print("channel error", h, e)


def peaks(path):
    BIN, bins = 20, {}
    for line in open(path, encoding="utf-8"):
        try:
            a = json.loads(line)["replayChatItemAction"]
            t = max(0, int(a["videoOffsetTimeMsec"]) // 1000)
            bins[t // BIN] = bins.get(t // BIN, 0) + sum(1 for x in a["actions"] if "addChatItemAction" in x)
        except Exception:
            continue
    score = {b: sum(bins.get(b + i, 0) for i in (-1, 0, 1)) for b in bins}
    out = []
    for b, s in sorted(score.items(), key=lambda x: -x[1]):
        if all(abs(b * BIN - o) > 120 for o, _ in out):
            out.append((b * BIN, s))
        if len(out) == PER_STREAM:
            break
    return out


def cut(url, s, e, folder, n):
    raw, out = f"{folder}/raw{n}.mp4", f"{folder}/clip{n}.mp4"
    run(YTDLP + ["--download-sections", f"*{s}-{e}", "--force-keyframes-at-cuts", "-f", "bv*[height<=1080]+ba/b",
                 "--merge-output-format", "mp4", "-o", raw, url])
    run(["ffmpeg", "-y", "-i", raw, "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", out])
    return out


def store(path, name):
    with open(path, "rb") as f:
        r = requests.post(f"{SB}/storage/v1/object/{BUCKET}/{name}", data=f, timeout=600,
                          headers={"apikey": SBK, "Authorization": f"Bearer {SBK}", "Content-Type": "video/mp4", "x-upsert": "true"})
    r.raise_for_status()
    return f"{SB}/storage/v1/object/public/{BUCKET}/{name}"


def to_youtube(path, title, desc):
    cid, sec, rt = E("GOOGLE_CLIENT_ID"), E("GOOGLE_CLIENT_SECRET"), E("YOUTUBE_REFRESH_TOKEN")
    if not (cid and sec and rt):
        return None
    tok = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cid, "client_secret": sec, "refresh_token": rt, "grant_type": "refresh_token"}).json().get("access_token")
    if not tok:
        print("YouTube login failed (token expired?)")
        return None
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=30,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(os.path.getsize(path))},
        json={"snippet": {"title": title[:95], "description": desc[:4900], "categoryId": "24"},
              "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}})
    if not init.ok:
        print("YouTube refused:", init.text[:300])
        return None
    with open(path, "rb") as f:
        up = requests.put(init.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
    return up.json().get("id") if up.ok else None


def process(row):
    url, vid = row["source_url"], vid_of(row["source_url"])
    if not vid:
        return fail(row, "bad link")
    items = yt("videos", part="snippet,contentDetails", id=vid)["items"]
    if not items:
        return fail(row, "video not found")
    v = items[0]
    if v["snippet"]["liveBroadcastContent"] != "none":
        print("still live, waiting:", url)
        return False
    if iso_seconds(v["contentDetails"]["duration"]) > MAX_HOURS * 3600:
        return fail(row, f"stream longer than {MAX_HOURS} hours")
    dur, title, chan = iso_seconds(v["contentDetails"]["duration"]), v["snippet"]["title"], v["snippet"]["channelTitle"]
    folder = tempfile.mkdtemp()
    run(YTDLP + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", f"{folder}/chat", url])
    files = list(pathlib.Path(folder).glob("chat*.json"))
    if not files:
        return fail(row, "no chat replay for this stream")
    top = peaks(files[0])
    if not top:
        return fail(row, "chat was empty")
    for n, (t, score) in enumerate(top, 1):
        s = max(0, t - 30)
        f = cut(url, s, min(dur, s + CLIP_LEN), folder, n)
        name = f"{vid}_{n}.mp4"
        data = {"title": f"{title} (moment {n})", "storage_path": name, "public_url": store(f, name)}
        if n == 1:
            db("PATCH", "clips", params={"id": f"eq.{row['id']}"}, json=data)
        else:
            db("POST", "clips", json={**data, "source_url": url})
        yid = to_youtube(f, f"{title[:60]} | Chat's top moment #{n}", f"Clip from {chan}\nSource: {url}&t={s}s\n#Shorts")
        print(f"clip {n}: chat score {score}, youtube id {yid}")
    return True


if __name__ == "__main__":
    if not (YT_KEY and SB and SBK):
        sys.exit("Missing secrets: need YOUTUBE_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY")
    discover()
    todo = db("GET", "clips", params={"public_url": "is.null", "storage_path": "is.null", "source_url": "not.is.null",
                                      "order": "created_at.asc", "limit": "5"})
    for row in todo:
        try:
            if process(row):
                break
        except Exception as e:
            fail(row, str(e))
            break
