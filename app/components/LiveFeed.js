import { View, Image, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from "react-native";
import { useState, useEffect, useRef } from "react";
import { getLiveFrame, getFrameUrl } from "../lib/api";

const CAMERAS = ["living_room", "mezzanine"];
const REFRESH_INTERVAL = 3000; // refresh every 3 seconds

export default function LiveFeed({ visible, onClose }) {
  const [camera, setCamera] = useState(CAMERAS[0]);
  const [frameUrl, setFrameUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (visible) {
      fetchFrame();
      intervalRef.current = setInterval(fetchFrame, REFRESH_INTERVAL);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [visible, camera]);

  async function fetchFrame() {
    try {
      setLoading(true);
      setError(false);
      const result = await getLiveFrame(camera);
      if (result && result.frame_url) {
        setFrameUrl(getFrameUrl(result.frame_url, true));
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  function toggleCamera() {
    const currentIndex = CAMERAS.indexOf(camera);
    const nextIndex = (currentIndex + 1) % CAMERAS.length;
    setCamera(CAMERAS[nextIndex]);
    setFrameUrl(null);
  }

  if (!visible) return null;

  const cameraLabel = camera === "living_room" ? "Living Room" : "Mezzanine";

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>LIVE</Text>
          <Text style={styles.cameraName}>{cameraLabel}</Text>
        </View>
        <View style={styles.headerRight}>
          <TouchableOpacity style={styles.toggleButton} onPress={toggleCamera}>
            <Text style={styles.toggleText}>Switch</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeText}>✕</Text>
          </TouchableOpacity>
        </View>
      </View>

      <View style={styles.imageContainer}>
        {frameUrl ? (
          <Image
            source={{ uri: frameUrl }}
            style={styles.image}
            resizeMode="contain"
          />
        ) : error ? (
          <Text style={styles.errorText}>Camera unavailable</Text>
        ) : (
          <ActivityIndicator size="large" color="#8E8E93" />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginHorizontal: 12,
    marginBottom: 8,
    backgroundColor: "#1C1C1E",
    borderRadius: 12,
    overflow: "hidden",
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  headerLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  headerRight: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#FF3B30",
  },
  liveText: {
    color: "#FF3B30",
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1,
  },
  cameraName: {
    color: "#8E8E93",
    fontSize: 12,
    marginLeft: 4,
  },
  toggleButton: {
    backgroundColor: "#2C2C2E",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 10,
  },
  toggleText: {
    color: "#0A84FF",
    fontSize: 12,
    fontWeight: "600",
  },
  closeButton: {
    paddingHorizontal: 4,
  },
  closeText: {
    color: "#636366",
    fontSize: 16,
  },
  imageContainer: {
    width: "100%",
    height: 220,
    backgroundColor: "#0A0A0A",
    justifyContent: "center",
    alignItems: "center",
  },
  image: {
    width: "100%",
    height: "100%",
  },
  errorText: {
    color: "#636366",
    fontSize: 14,
  },
});
