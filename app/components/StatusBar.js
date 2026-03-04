import { View, Text, StyleSheet } from "react-native";
import { useEffect, useState } from "react";
import { getStatus } from "../lib/api";

export default function StatusBar() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, []);

  async function fetchStatus() {
    try {
      const data = await getStatus();
      setStatus(data);
      setError(false);
    } catch {
      setError(true);
    }
  }

  if (error) {
    return (
      <View style={[styles.container, styles.offline]}>
        <View style={styles.dot} />
        <Text style={styles.offlineText}>Gabriel is offline</Text>
      </View>
    );
  }

  if (!status) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>Connecting...</Text>
      </View>
    );
  }

  const today = status.today || {};
  const latest = status.latest_event;
  const lastActivity = latest ? latest.activity : "—";

  return (
    <View style={styles.container}>
      <View style={[styles.dot, styles.dotOnline]} />
      <Text style={styles.text}>
        Online
      </Text>
      <Text style={styles.separator}>·</Text>
      <Text style={styles.text}>
        {today.total_events || 0} events today
      </Text>
      {latest && (
        <>
          <Text style={styles.separator}>·</Text>
          <Text style={styles.text}>Last: {lastActivity}</Text>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: "#1C1C1E",
    borderBottomWidth: 0.5,
    borderBottomColor: "#2C2C2E",
  },
  offline: {
    backgroundColor: "#3A1C1C",
  },
  dot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: "#636366",
    marginRight: 8,
  },
  dotOnline: {
    backgroundColor: "#30D158",
  },
  text: {
    color: "#8E8E93",
    fontSize: 12,
  },
  offlineText: {
    color: "#FF6961",
    fontSize: 12,
  },
  separator: {
    color: "#48484A",
    fontSize: 12,
    marginHorizontal: 6,
  },
});
