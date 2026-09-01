"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useStudioStore } from "@/lib/store";

type ConnectionState = "connecting" | "connected" | "disconnected";
type GenerationRequest = { prompt: string; aspect_ratio: "16:9" | "9:16"; reference_name?: string };
type SocketEvent = { type?: string; status?: string; progress?: number; message?: string; node?: string; preview_url?: string; video_url?: string; mp4_url?: string };

export function useVideoSocket(url = "ws://localhost:8000/ws/generation") {
  const socket = useRef<WebSocket | null>(null);
  const progressMessageId = useRef<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const addMessage = useStudioStore((state) => state.addMessage);
  const updateProgress = useStudioStore((state) => state.updateProgress);
  const handleEvent = useEffectEvent((event: SocketEvent) => {
    const progress = Math.round(event.progress ?? 0);
    const label = event.message ?? event.node ?? event.status ?? "Preparing generation";
    if (event.type === "complete" || event.type === "completion" || event.video_url || event.mp4_url) {
      addMessage({ id: crypto.randomUUID(), role: "video", content: "Generated clip", videoUrl: event.video_url ?? event.mp4_url, createdAt: new Date().toISOString() });
      progressMessageId.current = null;
    } else if (event.type === "preview" && event.preview_url) {
      addMessage({ id: crypto.randomUUID(), role: "system", content: "Preview frame received", createdAt: new Date().toISOString() });
    } else if (progressMessageId.current) updateProgress(progressMessageId.current, label, progress);
  });
  useEffect(() => {
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      setConnection("connecting"); const instance = new WebSocket(url); socket.current = instance;
      instance.onopen = () => setConnection("connected");
      instance.onmessage = (message) => { try { handleEvent(JSON.parse(message.data) as SocketEvent); } catch { /* Ignore non-JSON frames. */ } };
      instance.onclose = () => { setConnection("disconnected"); retry = setTimeout(connect, 3000); };
      instance.onerror = () => instance.close();
    };
    connect(); return () => { if (retry) clearTimeout(retry); socket.current?.close(); };
  }, [url]);
  function sendGeneration(request: GenerationRequest) {
    const id = crypto.randomUUID(); progressMessageId.current = id;
    addMessage({ id, role: "progress", content: "Queued for local render", progress: 0, createdAt: new Date().toISOString() });
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(request));
    else updateProgress(id, "Waiting for the local engine connection", 0);
  }
  return { connection, sendGeneration };
}
