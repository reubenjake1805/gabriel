import { View, Text, Image, TouchableOpacity, StyleSheet, Dimensions } from "react-native";
import { getFrameUrl } from "../lib/api";

const screenWidth = Dimensions.get("window").width;

export default function ChatBubble({ message }) {
  const isUser = message.role === "user";

  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowGabriel]}>
      <View
        style={[
          styles.bubble,
          isUser ? styles.bubbleUser : styles.bubbleGabriel,
        ]}
      >
        <Text style={[styles.text, isUser ? styles.textUser : styles.textGabriel]}>
          {message.content}
        </Text>

        {/* Frame thumbnails */}
        {message.frames && message.frames.length > 0 && (
          <View style={styles.framesRow}>
            {message.frames.slice(0, 4).map((frame, i) => {
              const url = getFrameUrl(frame.url);
              if (!url) return null;
              return (
                <Image
                  key={i}
                  source={{ uri: url }}
                  style={styles.thumbnail}
                  resizeMode="cover"
                />
              );
            })}
          </View>
        )}

        {/* Timestamp */}
        <Text style={[styles.time, isUser ? styles.timeUser : styles.timeGabriel]}>
          {message.time}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    marginVertical: 4,
    paddingHorizontal: 12,
  },
  rowUser: {
    alignItems: "flex-end",
  },
  rowGabriel: {
    alignItems: "flex-start",
  },
  bubble: {
    maxWidth: screenWidth * 0.78,
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  bubbleUser: {
    backgroundColor: "#2C2C2E",
    borderBottomRightRadius: 4,
  },
  bubbleGabriel: {
    backgroundColor: "#1C1C1E",
    borderBottomLeftRadius: 4,
  },
  text: {
    fontSize: 16,
    lineHeight: 22,
  },
  textUser: {
    color: "#FFFFFF",
  },
  textGabriel: {
    color: "#E5E5E7",
  },
  framesRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 10,
  },
  thumbnail: {
    width: 72,
    height: 72,
    borderRadius: 8,
    backgroundColor: "#3A3A3C",
  },
  time: {
    fontSize: 11,
    marginTop: 6,
  },
  timeUser: {
    color: "#8E8E93",
    textAlign: "right",
  },
  timeGabriel: {
    color: "#636366",
  },
});
