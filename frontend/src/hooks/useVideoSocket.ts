"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { resolveMediaUrl, resolveVideoUrl, useStudioStore } from "@/lib/store";

type ConnectionState = "connecting" | "connected" | "disconnected";
export type ImageStyle = "photo" | "3d" | "graphic" | "art" | "gif";
export type VoiceModel = "off" | "kokoro" | "qwen-base";
export type VoiceGender = "female" | "male";
export type VoiceSettings = { model: VoiceModel; gender: VoiceGender; script: string };
export type VoiceStatus = { available: string[]; optional: string[]; default: VoiceModel; references: { female: boolean; male: boolean }; install_hint: string; voices_dir?: string };
type VideoGenerationRequest = {
  generation_type: "video";
  prompt: string;
  aspect_ratio: "16:9" | "9:16";
  quality: "draft" | "final";
  model: "wan-2.1" | "ltx-video";
  width: number;
  height: number;
  frames: number;
  steps: number;
  cfg: number;
  fps: number;
  seed: number;
  reference_name?: string;
  voice: VoiceSettings;
};
type ImageGenerationRequest = { generation_type: "image"; prompt: string; style: ImageStyle; aspect_ratio: "1:1" | "16:9" | "9:16"; width: number; height: number; steps: number; seed: number };
type GenerationRequest = VideoGenerationRequest | ImageGenerationRequest;
type SocketEvent = { type?: string; status?: string; progress?: number; message?: string; node?: string; preview_url?: string; image_url?: string; video_url?: string; mp4_url?: string };
const apiUrl = "http://localhost:8000";
export const defaultVoiceSettings: VoiceSettings = { model: "off", gender: "female", script: "" };

export async function fetchVoiceStatus(): Promise<VoiceStatus> {
  const response = await fetch(`${apiUrl}/voice`);
  if (!response.ok) throw new Error("Could not load voice engines");
  return response.json() as Promise<VoiceStatus>;
}

export function useVideoSocket(url = "ws://localhost:8000/ws/generation") {
  const socket = useRef<WebSocket | null>(null);
  const progressMessageId = useRef<string | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [isGenerating, setIsGenerating] = useState(false);
  const addMessage = useStudioStore((state) => state.addMessage);
  const replaceMessage = useStudioStore((state) => state.replaceMessage);
  const updateProgress = useStudioStore((state) => state.updateProgress);
  const handleEvent = useEffectEvent((event: SocketEvent) => {
    const progress = Math.round(event.progress ?? 0);
    const label = event.message ?? event.node ?? event.status ?? "Preparing generation";
    if (event.image_url) {
      const message = { id: progressMessageId.current ?? crypto.randomUUID(), role: "image" as const, content: "Generated image", imageUrl: resolveMediaUrl(event.image_url), createdAt: new Date().toISOString() };
      if (progressMessageId.current) replaceMessage(progressMessageId.current, message); else addMessage(message);
      progressMessageId.current = null; setIsGenerating(false);
    } else if (event.type === "complete" || event.type === "completion" || event.video_url || event.mp4_url) {
      const message = { id: progressMessageId.current ?? crypto.randomUUID(), role: "video" as const, content: "Generated clip", videoUrl: resolveVideoUrl(event.video_url ?? event.mp4_url), createdAt: new Date().toISOString() };
      if (progressMessageId.current) replaceMessage(progressMessageId.current, message); else addMessage(message);
      progressMessageId.current = null; setIsGenerating(false);
    } else if (event.type === "cancelled" || event.type === "error") {
      if (progressMessageId.current) updateProgress(progressMessageId.current, event.message ?? "Generation stopped", 0);
      progressMessageId.current = null; setIsGenerating(false);
    } else if (event.type === "preview" && event.preview_url) {
      addMessage({ id: crypto.randomUUID(), role: "system", content: "Preview frame received", createdAt: new Date().toISOString() });
    } else if (progressMessageId.current) updateProgress(progressMessageId.current, label, progress);
  });
  useEffect(() => {
    let retry: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    const connect = () => {
      if (disposed) return;
      setConnection("connecting"); const instance = new WebSocket(url); socket.current = instance;
      instance.onopen = () => setConnection("connected");
      instance.onmessage = (message) => { try { handleEvent(JSON.parse(message.data) as SocketEvent); } catch { /* Ignore non-JSON frames. */ } };
      instance.onclose = () => { if (disposed) return; setConnection("disconnected"); retry = setTimeout(connect, 3000); };
      instance.onerror = () => instance.close();
    };
    connect(); return () => { disposed = true; if (retry) clearTimeout(retry); socket.current?.close(); };
  }, [url]);
  function sendGeneration(request: GenerationRequest, estimatedTime: string) {
    const id = crypto.randomUUID(); progressMessageId.current = id; setIsGenerating(true);
    addMessage({ id, role: "progress", content: `Queued for local render · estimated ${estimatedTime}`, progress: 0, createdAt: new Date().toISOString() });
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(request));
    else updateProgress(id, "Waiting for the local engine connection", 0);
  }
  async function controlGeneration(action: "cancel" | "clear-queue") {
    const response = await fetch(`${apiUrl}/generation/${action}`, { method: "POST" });
    if (!response.ok) throw new Error("Could not update the ComfyUI queue");
    if (action === "cancel") setIsGenerating(false);
  }
  return { connection, isGenerating, sendGeneration, controlGeneration };
}
