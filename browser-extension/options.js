const api = globalThis.browser ?? globalThis.chrome;
const DEFAULTS = { port: 47821, token: "", preset: "" };
const $ = (id) => document.getElementById(id);

function status(text, ok) {
  $("status").textContent = text;
  $("status").className = ok ? "ok" : "err";
}

async function load() {
  const cfg = { ...DEFAULTS, ...(await api.storage.local.get(DEFAULTS)) };
  $("token").value = cfg.token;
  $("port").value = cfg.port;
  $("preset").value = cfg.preset;
}

async function save() {
  const port = parseInt($("port").value, 10) || DEFAULTS.port;
  await api.storage.local.set({ token: $("token").value.trim(), port, preset: $("preset").value });
  status("Saved.", true);
}

async function test() {
  await save();
  const { port, token } = await api.storage.local.get(DEFAULTS);
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/ping`, { headers: { "X-Downloader-Token": token } });
    if (resp.ok) {
      const info = await resp.json();
      status(`Connected to Downloader ${info.version}.`, true);
    } else {
      status(resp.status === 401 ? "Token rejected." : `Error ${resp.status}.`, false);
    }
  } catch (_err) {
    status("Downloader is not running, or the port is wrong.", false);
  }
}

$("save").addEventListener("click", save);
$("test").addEventListener("click", test);
load();
