const INDEX_HTML = new TextDecoder().decode(Uint8Array.from(atob("__INDEX_HTML_BASE64__"), c => c.charCodeAt(0)));
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const safe = (value) => typeof value === "string" && value.length > 0 && value.length < 220 && !value.includes("..") && !value.includes("/") && !value.includes("\\\\");

async function setup(env) {
  await env.DB.batch([
    env.DB.prepare("CREATE TABLE IF NOT EXISTS descriptions (filename TEXT PRIMARY KEY, text TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"),
    env.DB.prepare("CREATE INDEX IF NOT EXISTS descriptions_visibility ON descriptions(archived, deleted)"),
  ]);
}

function contentType(name) {
  const ext = name.split(".").pop().toLowerCase();
  return ({ png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", bmp: "image/bmp" })[ext] || "application/octet-stream";
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    if (request.method === "GET" && (path === "/" || path === "/index.html")) return new Response(INDEX_HTML, { headers: { "content-type": "text/html; charset=utf-8" } });
    if (!path.startsWith("/api/") && path !== "/save" && !path.startsWith("/img/")) return new Response("Not found", { status: 404 });
    try {
      await setup(env);
      if (request.method === "GET" && path === "/api/images") {
        const listed = await env.IMAGES.list({ prefix: "paste:" });
        const hidden = await env.DB.prepare("SELECT filename, text FROM descriptions WHERE archived = 1 OR deleted = 1").all();
        const skip = new Set(hidden.results.map(x => x.filename));
        const descs = await env.DB.prepare("SELECT filename, text FROM descriptions").all();
        const description = new Map(descs.results.map(x => [x.filename, x.text]));
        const items = listed.objects.filter(o => !skip.has(o.key.slice(6))).map(o => ({ name: o.key.slice(6), src: o.key, url: "/img/" + encodeURIComponent(o.key), mtime: o.uploaded.getTime() / 1000, size: o.size, desc: description.get(o.key.slice(6)) || "" })).sort((a,b) => b.mtime - a.mtime);
        return json(items);
      }
      if (request.method === "GET" && path === "/api/desc") {
        const rows = await env.DB.prepare("SELECT filename, text, archived FROM descriptions WHERE deleted = 0").all();
        return json({ descriptions: Object.fromEntries(rows.results.filter(r => r.text).map(r => [r.filename, r.text])), archived: rows.results.filter(r => r.archived).map(r => r.filename) });
      }
      if (request.method === "GET" && path === "/api/status") return json({ hosted: true, screenshots_dir: "Paste or upload screenshots into this page", capture_dir: "Hosted image library", export_dir: "Browser downloads", watched_exists: false });
      if (request.method === "GET" && path.startsWith("/img/")) {
        const key = decodeURIComponent(path.slice(5));
        if (!key.startsWith("paste:") || !safe(key.slice(6))) return json({ ok:false, error:"not found" }, 404);
        const object = await env.IMAGES.get(key);
        if (!object) return json({ ok:false, error:"not found" }, 404);
        return new Response(object.body, { headers: { "content-type": object.httpMetadata?.contentType || contentType(key), "cache-control":"public, max-age=31536000, immutable" } });
      }
      const body = await request.json();
      if (request.method === "POST" && path === "/api/desc") {
        if (!safe(body.filename)) return json({ ok:false, error:"invalid filename" }, 400);
        const text = String(body.text || "").trim();
        if (text) await env.DB.prepare("INSERT INTO descriptions(filename,text,created_at) VALUES(?,?,?) ON CONFLICT(filename) DO UPDATE SET text=excluded.text").bind(body.filename,text,Date.now()).run();
        else await env.DB.prepare("DELETE FROM descriptions WHERE filename = ?").bind(body.filename).run();
        return json({ok:true});
      }
      if (request.method === "POST" && path === "/api/actions") {
        const srcs = Array.isArray(body.srcs) ? body.srcs : [];
        const names = srcs.map(s => String(s).replace(/^paste:/, "")).filter(safe);
        if (body.action === "clear_all") { const all = await env.IMAGES.list({prefix:"paste:"}); names.push(...all.objects.map(o => o.key.slice(6))); }
        if (!['archive','clear_archive','delete','clear_all'].includes(body.action)) return json({ok:false,error:"unknown action"},400);
        for (const name of names) {
          if (body.action === 'clear_archive') await env.DB.prepare("UPDATE descriptions SET archived=0 WHERE filename=?").bind(name).run();
          else if (body.action === 'archive') await env.DB.prepare("INSERT INTO descriptions(filename,text,archived,created_at) VALUES(?,?,1,?) ON CONFLICT(filename) DO UPDATE SET archived=1,deleted=0").bind(name,"",Date.now()).run();
          else await env.DB.prepare("INSERT INTO descriptions(filename,text,deleted,created_at) VALUES(?,?,1,?) ON CONFLICT(filename) DO UPDATE SET deleted=1,archived=0,text='' ").bind(name,"",Date.now()).run();
        }
        return json({ok:true});
      }
      if (request.method === "POST" && path === "/save") {
        const dataUrl = String(body.image || ""); const match = dataUrl.match(/^data:(image\/(png|jpeg|gif|webp|bmp));base64,(.+)$/);
        if (!match) return json({ok:false,error:"bad payload"},400);
        const extension = match[2] === 'jpeg' ? 'jpg' : match[2]; const name = `paste_${Date.now()}_${crypto.randomUUID().slice(0,8)}.${extension}`;
        const bytes = Uint8Array.from(atob(match[3]), c => c.charCodeAt(0));
        await env.IMAGES.put(`paste:${name}`, bytes, { httpMetadata:{contentType:match[1]} });
        return json({ok:true,file:name});
      }
      if (request.method === "POST" && path === "/api/export") {
        const items = Array.isArray(body.items) ? body.items : []; if (!items.length) return json({ok:false,error:"nothing to export"},400);
        const markdown = ['# Erol — Screenshot issue handoff', '', `Generated: ${new Date().toISOString()}`, '', ...items.map((it,i) => `## ${i+1}. ${String(it.src||'').replace(/^paste:/,'')}\n- Description: ${(it.desc||'').trim() || '*(no description provided)*'}`), '', '---', 'Review each screenshot and its description in order.'].join('\n');
        return json({ok:true,bundle_dir:"Your browser will download the handoff text.",bundle_name:"Erol handoff",markdown,images:[],count:items.length});
      }
      return json({ok:false,error:"not found"},404);
    } catch (error) { return json({ok:false,error:String(error.message || error)},500); }
  }
};
