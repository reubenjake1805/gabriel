import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  Keyboard,
  Platform,
  StyleSheet,
  ActivityIndicator,
  Dimensions,
} from "react-native";
import { useState, useRef, useEffect, useCallback } from "react";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";

import ChatBubble from "../../components/ChatBubble";
import StatusBar from "../../components/StatusBar";
import ImageViewer from "../../components/ImageViewer";
import LiveFeed from "../../components/LiveFeed";
import { askGabriel } from "../../lib/api";

export default function ChatScreen() {
  const insets = useSafeAreaInsets();
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
  const [showLive, setShowLive] = useState(false);
  const [viewerImage, setViewerImage] = useState(null);
  const [kbHeight, setKbHeight] = useState(0);
  const flatListRef = useRef(null);

  useEffect(() => {
    const sub1 = Keyboard.addListener(
      Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow",
      (e) => setKbHeight(e.endCoordinates.height)
    );
    const sub2 = Keyboard.addListener(
      Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide",
      () => setKbHeight(0)
    );
    return () => { sub1.remove(); sub2.remove(); };
  }, []);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 150);
  }, []);

  useEffect(() => { scrollToEnd(); }, [messages, kbHeight]);

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
    setShowLive(false);

    const userMsg = {
      id: `user-${Date.now()}`,
      role: "user",
      content: question,
      time: formatTime(),
      frames: [],
    };
    setMessages((prev) => [...prev, userMsg]);
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

  // On Android, when keyboard is visible, we reduce the overall height
  // by the keyboard height so the input bar stays visible
  const bottomOffset = Platform.OS === "android" && kbHeight > 0
    ? kbHeight - insets.bottom
    : 0;

  return (
    <View style={styles.outerContainer}>
      <SafeAreaView style={styles.safe} edges={["top"]}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Gabriel</Text>
          <Text style={styles.headerSubtitle}>Watching over Lee 🐱</Text>
        </View>

        {/* Status bar */}
        <StatusBar />

        {/* Main content area that shrinks when keyboard appears */}
        <View style={[styles.content, { marginBottom: bottomOffset }]}>
          {/* Chat messages */}
          <FlatList
            ref={flatListRef}
            data={messages}
            keyExtractor={(item) => item.id}
            style={styles.chatList}
            renderItem={({ item }) => (
              <ChatBubble
                message={item}
                onImagePress={(url) => setViewerImage(url)}
              />
            )}
            contentContainerStyle={styles.messageList}
            keyboardShouldPersistTaps="handled"
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

          {/* Live feed */}
          <LiveFeed
            visible={showLive}
            onClose={() => setShowLive(false)}
          />

          {/* Input bar */}
          <View style={[
            styles.inputBar,
            { paddingBottom: kbHeight > 0 ? 8 : Math.max(insets.bottom, 8) }
          ]}>
            <TouchableOpacity
              style={[styles.liveButton, showLive && styles.liveButtonActive]}
              onPress={() => setShowLive(!showLive)}
            >
              <Text style={styles.liveButtonText}>📷</Text>
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
        </View>
      </SafeAreaView>

      {/* Full-screen image viewer */}
      <ImageViewer
        visible={!!viewerImage}
        imageUrl={viewerImage}
        onClose={() => setViewerImage(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  outerContainer: {
    flex: 1,
    backgroundColor: "#000000",
  },
  safe: {
    flex: 1,
    backgroundColor: "#000000",
  },
  content: {
    flex: 1,
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
  chatList: {
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
  inputBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingTop: 8,
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
  liveButtonActive: {
    backgroundColor: "#0A84FF",
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
