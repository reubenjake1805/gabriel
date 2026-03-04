import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  ActivityIndicator,
  Image,
} from "react-native";
import { useState, useRef } from "react";
import { SafeAreaView } from "react-native-safe-area-context";

import ChatBubble from "../../components/ChatBubble";
import StatusBar from "../../components/StatusBar";
import { askGabriel, getLiveFrame, getFrameUrl } from "../../lib/api";

export default function ChatScreen() {
  const [messages, setMessages] = useState([
    {
      id: "welcome",
      role: "gabriel",
      content:
        "Hey! I'm Gabriel, Lee's guardian angel. Ask me anything about how Lee's doing — \"Has Lee eaten today?\", \"Is Lee okay?\", or just \"What's Lee up to?\" 🐱",
      time: "",
      frames: [],
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [liveFrame, setLiveFrame] = useState(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const flatListRef = useRef(null);

  function formatTime() {
    const now = new Date();
    return now.toLocaleTimeString("en-IN", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  }

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setLiveFrame(null);

    // Add user message
    const userMsg = {
      id: `user-${Date.now()}`,
      role: "user",
      content: question,
      time: formatTime(),
      frames: [],
    };
    setMessages((prev) => [...prev, userMsg]);

    // Show typing indicator
    setLoading(true);

    try {
      const result = await askGabriel(question);

      const gabrielMsg = {
        id: `gabriel-${Date.now()}`,
        role: "gabriel",
        content: result.answer,
        time: formatTime(),
        frames: result.frames || [],
      };
      setMessages((prev) => [...prev, gabrielMsg]);
    } catch (err) {
      const errorMsg = {
        id: `error-${Date.now()}`,
        role: "gabriel",
        content:
          "I can't reach the server right now. Make sure Gabriel is running on your MacBook and the tunnel is active.",
        time: formatTime(),
        frames: [],
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  }

  async function handleLiveView() {
    setLiveLoading(true);
    try {
      const result = await getLiveFrame();
      setLiveFrame(result);
    } catch {
      setLiveFrame(null);
    } finally {
      setLiveLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.container}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Gabriel</Text>
          <Text style={styles.headerSubtitle}>Watching over Lee 🐱</Text>
        </View>

        {/* Status bar */}
        <StatusBar />

        {/* Chat messages */}
        <KeyboardAvoidingView
          style={styles.chatArea}
          behavior={Platform.OS === "ios" ? "padding" : "height"}
          keyboardVerticalOffset={0}
        >
          <FlatList
            ref={flatListRef}
            data={messages}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <ChatBubble message={item} />}
            contentContainerStyle={styles.messageList}
            onContentSizeChange={() =>
              flatListRef.current?.scrollToEnd({ animated: true })
            }
            ListFooterComponent={
              loading ? (
                <View style={styles.typingRow}>
                  <View style={styles.typingBubble}>
                    <ActivityIndicator size="small" color="#8E8E93" />
                    <Text style={styles.typingText}>Gabriel is thinking...</Text>
                  </View>
                </View>
              ) : null
            }
          />

          {/* Live frame preview */}
          {liveFrame && liveFrame.frame_url && (
            <View style={styles.livePreview}>
              <View style={styles.liveHeader}>
                <Text style={styles.liveLabel}>📷 Live — {liveFrame.camera}</Text>
                <TouchableOpacity onPress={() => setLiveFrame(null)}>
                  <Text style={styles.liveClose}>✕</Text>
                </TouchableOpacity>
              </View>
              <Image
                source={{ uri: getFrameUrl(liveFrame.frame_url) }}
                style={styles.liveImage}
                resizeMode="contain"
              />
            </View>
          )}

          {/* Input bar */}
          <View style={styles.inputBar}>
            <TouchableOpacity
              style={styles.liveButton}
              onPress={handleLiveView}
              disabled={liveLoading}
            >
              {liveLoading ? (
                <ActivityIndicator size="small" color="#8E8E93" />
              ) : (
                <Text style={styles.liveButtonText}>📷</Text>
              )}
            </TouchableOpacity>

            <TextInput
              style={styles.input}
              value={input}
              onChangeText={setInput}
              placeholder="Ask about Lee..."
              placeholderTextColor="#636366"
              returnKeyType="send"
              onSubmitEditing={handleSend}
              editable={!loading}
            />

            <TouchableOpacity
              style={[
                styles.sendButton,
                (!input.trim() || loading) && styles.sendButtonDisabled,
              ]}
              onPress={handleSend}
              disabled={!input.trim() || loading}
            >
              <Text style={styles.sendButtonText}>↑</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: "#000000",
  },
  container: {
    flex: 1,
    backgroundColor: "#000000",
  },
  header: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 8,
    backgroundColor: "#000000",
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: "700",
    color: "#FFFFFF",
  },
  headerSubtitle: {
    fontSize: 14,
    color: "#8E8E93",
    marginTop: 2,
  },
  chatArea: {
    flex: 1,
  },
  messageList: {
    paddingVertical: 12,
  },
  typingRow: {
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
  typingBubble: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#1C1C1E",
    borderRadius: 18,
    paddingHorizontal: 16,
    paddingVertical: 10,
    alignSelf: "flex-start",
    gap: 8,
  },
  typingText: {
    color: "#8E8E93",
    fontSize: 14,
  },
  livePreview: {
    marginHorizontal: 12,
    marginBottom: 8,
    backgroundColor: "#1C1C1E",
    borderRadius: 12,
    overflow: "hidden",
  },
  liveHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  liveLabel: {
    color: "#8E8E93",
    fontSize: 12,
  },
  liveClose: {
    color: "#636366",
    fontSize: 16,
    paddingHorizontal: 4,
  },
  liveImage: {
    width: "100%",
    height: 200,
    backgroundColor: "#2C2C2E",
  },
  inputBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 8,
    paddingBottom: Platform.OS === "ios" ? 24 : 8,
    backgroundColor: "#000000",
    borderTopWidth: 0.5,
    borderTopColor: "#1C1C1E",
    gap: 8,
  },
  liveButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "#1C1C1E",
    justifyContent: "center",
    alignItems: "center",
  },
  liveButtonText: {
    fontSize: 18,
  },
  input: {
    flex: 1,
    backgroundColor: "#1C1C1E",
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 16,
    color: "#FFFFFF",
    maxHeight: 100,
  },
  sendButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "#0A84FF",
    justifyContent: "center",
    alignItems: "center",
  },
  sendButtonDisabled: {
    backgroundColor: "#1C1C1E",
  },
  sendButtonText: {
    color: "#FFFFFF",
    fontSize: 18,
    fontWeight: "700",
  },
});
