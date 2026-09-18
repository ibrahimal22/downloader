// Sends links to the Downloader desktop app over its localhost endpoint.
const api = globalThis.browser ?? globalThis.chrome;

const DEFAULTS = { port: 47821, token: "", preset: "" };

async function config() {
  return { ...DEFAULTS, ...(await api.storage.local.get(DEFAULTS)) };
}

function notify(message) {
  api.notifications?.create({
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "Downloader",
    message,
  });
}

async function send(url, quick) {
  if (!/^https?:\/\//i.test(url)) {
    notify("Only http(s) links can be downloaded.");
    return;
  }
  const { port, token, preset } = await config();
  if (!token) {
    notify("Open the extension options and paste the pairing token from Downloader → Settings → Integrations.");
    api.runtime.openOptionsPage();
    return;
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Downloader-Token": token },
      body: JSON.stringify({ url, quick, preset }),
    });
    if (resp.status === 401) notify("Pairing token rejected. Copy it again from Downloader's settings.");
    else if (!resp.ok) notify(`Downloader returned an error (${resp.status}).`);
    else if (quick) notify("Queued in Downloader.");
  } catch (_err) {
    // App not running: the downloader:// protocol handler starts it with the link.
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    if (tab?.id !== undefined) {
      api.tabs.update(tab.id, { url: `downloader://add?url=${encodeURIComponent(url)}` });
    }
  }
}

function createMenus() {
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({
      id: "dl-open",
      title: "Download with Downloader…",
      contexts: ["link", "video", "audio", "page"],
    });
    api.contextMenus.create({
      id: "dl-quick",
      title: "Quick download (default quality)",
      contexts: ["link", "video", "audio", "page"],
    });
  });
}

api.runtime.onInstalled.addListener(createMenus);
api.runtime.onStartup?.addListener(createMenus);

api.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.linkUrl || info.srcUrl || info.pageUrl || tab?.url;
  if (url) send(url, info.menuItemId === "dl-quick");
});

api.action.onClicked.addListener((tab) => {
  if (tab?.url) send(tab.url, false);
});
