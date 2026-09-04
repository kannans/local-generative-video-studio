import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type AspectRatio = "1:1" | "16:9" | "9:16";
export type MessageRole = "user" | "system" | "progress" | "video" | "image";
export interface StudioMessage { id: string; role: MessageRole; content: string; createdAt: string; progress?: number; videoUrl?: string; imageUrl?: string }
export interface StudioSession { id: string; title: string; updatedLabel: string }
export interface PlaybackState { playing: boolean; loop: boolean; currentTime: number; duration: number }
export interface MaskStroke { points: Array<{ x: number; y: number }>; size: number }

const apiUrl = "http://localhost:8000";
export function resolveMediaUrl(url: string | undefined) {
  if (!url || !url.startsWith("/")) return url;
  return `${apiUrl}${url}`;
}
export const resolveVideoUrl = resolveMediaUrl;

interface StudioState {
  currentSessionId: string; sessions: StudioSession[]; messages: StudioMessage[];
  playback: Record<string, PlaybackState>; masks: Record<string, MaskStroke[]>;
  createSession: () => void; setCurrentSession: (id: string) => void; clearSessions: () => void;
  addMessage: (message: StudioMessage) => void; updateProgress: (id: string, content: string, progress: number) => void;
  setPlayback: (id: string, update: Partial<PlaybackState>) => void; setMask: (id: string, strokes: MaskStroke[]) => void;
}
const initial = { id: "session-1", title: "Untitled motion study", updatedLabel: "Just now" };
export const useStudioStore = create<StudioState>()(persist((set) => ({
  currentSessionId: initial.id, sessions: [initial], messages: [], playback: {}, masks: {},
  createSession: () => set((state) => { const session = { id: crypto.randomUUID(), title: "New motion study", updatedLabel: "Just now" }; return { sessions: [session, ...state.sessions], currentSessionId: session.id, messages: [] }; }),
  setCurrentSession: (id) => set({ currentSessionId: id }),
  clearSessions: () => set({ sessions: [initial], currentSessionId: initial.id, messages: [], playback: {}, masks: {} }),
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
  updateProgress: (id, content, progress) => set((state) => ({ messages: state.messages.map((message) => message.id === id ? { ...message, content, progress } : message) })),
  setPlayback: (id, update) => set((state) => {
    const previous = state.playback[id] ?? { playing: false, loop: false, currentTime: 0, duration: 0 };
    return { playback: { ...state.playback, [id]: { ...previous, ...update } } };
  }),
  setMask: (id, strokes) => set((state) => ({ masks: { ...state.masks, [id]: strokes } })),
}), {
  name: "framefoundry-studio",
  storage: createJSONStorage(() => localStorage),
  skipHydration: true,
  partialize: (state) => ({
    currentSessionId: state.currentSessionId,
    sessions: state.sessions,
    messages: state.messages.filter((message) => message.role !== "progress"),
    masks: state.masks,
  }),
  merge: (persistedState, currentState) => {
    const persisted = persistedState as Partial<StudioState>;
    const videoUrls = new Set<string>();
    const messages = (persisted.messages ?? []).flatMap((message) => {
      const videoUrl = resolveVideoUrl(message.videoUrl);
      const imageUrl = resolveMediaUrl(message.imageUrl);
      if (message.role === "video" && videoUrl) {
        if (videoUrls.has(videoUrl)) return [];
        videoUrls.add(videoUrl);
      }
      return [{ ...message, videoUrl, imageUrl }];
    });
    return { ...currentState, ...persisted, messages };
  },
}));
