// Cloudflare Worker: forces a real download of Bristol Politics Show audio.
//
// Radio4All serves the MP3s cross-origin with no "save this" header, so a plain
// link just plays them. This Worker re-serves a file with a
// Content-Disposition: attachment header, which every browser obeys — so the
// site's Download buttons pop a proper save dialog everywhere.
//
// Deploy on Cloudflare's free Workers plan, then give the resulting
// *.workers.dev URL to wire into the website (DOWNLOAD_PROXY in _includes/player.html).
//
// Usage:  https://<worker-url>/?url=<radio4all mp3 url>&name=<filename.mp3>

const ALLOWED_PREFIX = "https://www.radio4all.net/files/";

export default {
  async fetch(request) {
    const { searchParams } = new URL(request.url);
    const target = searchParams.get("url");
    const name = (searchParams.get("name") || "bristol-politics-show.mp3").slice(0, 120);

    // Only proxy Radio4All audio — never act as an open proxy.
    if (!target || !target.startsWith(ALLOWED_PREFIX)) {
      return new Response("Not allowed", { status: 400 });
    }

    // Pass the Range header through so big downloads can resume / show progress.
    const range = request.headers.get("Range");
    const upstream = await fetch(target, { headers: range ? { Range: range } : {} });

    const headers = new Headers(upstream.headers);
    const asciiName = name.replace(/[^\x20-\x7E]/g, "_").replace(/["\\]/g, "");
    headers.set(
      "Content-Disposition",
      `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(name)}`
    );
    headers.set("Access-Control-Allow-Origin", "*");
    if (!headers.get("Content-Type")) headers.set("Content-Type", "audio/mpeg");

    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
