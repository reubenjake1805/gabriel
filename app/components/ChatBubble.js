import { View, Text, Image, TouchableOpacity, StyleSheet, Dimensions } from "react-native";
import { useState } from "react";
import { Audio } from "expo-av";
import { getFrameUrl, getAudioUrl } from "../lib/api";

const screenWidth = Dimensions.get("window").width;

export default function ChatBubble({ message, onImagePress }) {
  const isUser = message.role === "user";
  const [playingAudio, setPlayingAudio] = useState(null);

  // Check if any frames are actually audio clips (WAV files)
  const imageFrames = (message.frames || []).filter(
    (f) => f.url && !f.url.endsWith(".wav")
  );
  const audioFrames = (message.frames || []).filter(
    (f) => f.url && f.url.endsWith(".wav")
  );

  async function playAudio(clipPath) {
    try {
      if (playingAudio) {
        await playingAudio.unloadAsync();
        setPlayingAudio(null);
      }
      const url = getAudioUrl(clipPath);
      if (!url) return;

      const { sound } = await Audio.Sound.createAsync({ uri: url });
      setPlayingAudio(sound);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.didJustFinish) {
          sound.unloadAsync();
          setPlayingAudio(null);
        }
      });
      await sound.playAsync();
    } catch (err) {
      console.error("Audio playback error:", err);
      setPlayingAudio(null);
    }
  }

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

        {/* Image thumbnails */}
        {imageFrames.length > 0 && (
          <View style={styles.framesRow}>
            {imageFrames.slice(0, 4).map((frame, i) => {
              const url = getFrameUrl(frame.url);
              if (!url) return null;
              return (
                <TouchableOpacity
                  key={i}
                  onPress={() => onImagePress && onImagePress(url)}
                  activeOpacity={0.7}
                >
                  <Image
                    source={{ uri: url }}
                    style={styles.thumbnail}
                    resizeMode="cover"
                  />
                </TouchableOpacity>
              );
            })}
          </View>
        )}

        {/* Audio clips */}
        {audioFrames.length > 0 && (
          <View style={styles.audioRow}>
            {audioFrames.map((frame, i) => (
              <TouchableOpacity
                key={`audio-${i}`}
                style={styles.audioButton}
                onPress={() => playAudio(frame.url)}
                activeOpacity={0.7}
              >
                <Text style={styles.audioIcon}>🔊</Text>
                <Text style={styles.audioLabel}>
                  {frame.activity === "vocalizing" ? "Sound clip" : frame.activity}
                </Text>
              </TouchableOpacity>
            ))}
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
  audioRow: {
    marginTop: 10,
    gap: 6,
  },
  audioButton: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#2C2C2E",
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 8,
  },
  audioIcon: {
    fontSize: 16,
  },
  audioLabel: {
    color: "#0A84FF",
    fontSize: 14,
    fontWeight: "500",
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
