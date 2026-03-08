/**
 * Gabriel — API Client
 */

const API_BASE = "https://api.gabrielcatwatch.com";

export async function askGabriel(question) {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

export async function getLiveFrame(camera = null) {
  const params = camera ? `?camera=${camera}` : "";
  const response = await fetch(`${API_BASE}/api/live${params}`);
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

export async function getStatus() {
  const response = await fetch(`${API_BASE}/api/status`);
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  return response.json();
}

export function getFrameUrl(framePath, bustCache = false) {
  if (!framePath) return null;
  const parts = framePath.split("/");
  const filename = parts[parts.length - 1];
  const date = parts[parts.length - 2];
  let url = `${API_BASE}/api/frames/${date}/${filename}`;
  if (bustCache) url += `?t=${Date.now()}`;
  return url;
}

export { API_BASE };
