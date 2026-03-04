/**
 * Gabriel — API Client
 *
 * Talks to the Gabriel API server running on your MacBook
 * via the Cloudflare tunnel.
 */

// UPDATE THIS to your Cloudflare tunnel URL
const API_BASE = "https://conditioning-puzzle-went-trained.trycloudflare.com";

/**
 * Ask Gabriel a question about Lee.
 */
export async function askGabriel(question) {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

/**
 * Get the latest live frame from a camera.
 */
export async function getLiveFrame(camera = null) {
  const params = camera ? `?camera=${camera}` : "";
  const response = await fetch(`${API_BASE}/api/live${params}`);

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

/**
 * Get system status and today's summary.
 */
export async function getStatus() {
  const response = await fetch(`${API_BASE}/api/status`);

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

/**
 * Get the full URL for a frame image.
 */
export function getFrameUrl(framePath) {
  if (!framePath) return null;
  // Extract date and filename from the full path
  const parts = framePath.split("/");
  const filename = parts[parts.length - 1];
  const date = parts[parts.length - 2];
  return `${API_BASE}/api/frames/${date}/${filename}`;
}

export { API_BASE };
