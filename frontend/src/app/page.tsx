"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Ban, ChevronDown, Clapperboard, History, Images, ImagePlus, ListX, Menu, Plus, Send, SlidersHorizontal, Sparkles, Trash2, Video, X } from "lucide-react";
import { ImageViewer } from "@/app/components/ImageViewer";
import { VideoViewer } from "@/components/player/VideoViewer";
import { ImageStyle, useVideoSocket } from "@/hooks/useVideoSocket";
import { AspectRatio, useStudioStore } from "@/lib/store";

const prompts = ["A silver tide cuts through black sand", "A sunlit train crossing a foggy valley", "A tiny botanical world growing on a desk"];
type VideoModel = "wan-2.1" | "ltx-video";
type GenerationMode = "image" | "video";
type ImageQuality = "draft" | "standard" | "high";
type GenerationSettings = { model: VideoModel; width: number; height: number; frames: number; steps: number; cfg: number; fps: number; seed: number };
type ImageSettings = { quality: ImageQuality; width: number; height: number; steps: number; seed: number };
const defaultGenerationSettings: GenerationSettings = { model: "wan-2.1", width: 512, height: 288, frames: 49, steps: 12, cfg: 5.0, fps: 16, seed: 73 };
const defaultImageSettings: ImageSettings = { quality: "standard", width: 1024, height: 576, steps: 4, seed: 73 };

function alignImageDimension(value: number) {
  return Math.max(64, value - (value % 16));
}

function estimatedRenderSeconds(settings: GenerationSettings) {
  const benchmarkWork = 512 * 288 * 49 * 12;
  const selectedWork = settings.width * settings.height * settings.frames * settings.steps;
  return Math.round(26 + 384.58 * selectedWork / benchmarkWork);
}

function formatDuration(seconds: number) {
  if (seconds < 90) return `${seconds} sec`;
  return `${Math.round(seconds / 60)} min`;
}

export default function Home() {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [drawer, setDrawer] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<GenerationMode>("video");
  const [imageStyle, setImageStyle] = useState<ImageStyle>("photo");
  const [ratio, setRatio] = useState<AspectRatio>("16:9");
  const [file, setFile] = useState<File | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [generationSettings, setGenerationSettings] = useState(defaultGenerationSettings);
  const [imageSettings, setImageSettings] = useState(defaultImageSettings);
  const [queueAction, setQueueAction] = useState<"cancel" | "clear-queue" | null>(null);
  const { sessions, currentSessionId, messages, createSession, clearSessions, addMessage, setCurrentSession } = useStudioStore();
  const { connection, controlGeneration, isGenerating, sendGeneration } = useVideoSocket();
  const renderEstimate = generationSettings.model === "ltx-video" ? "not calibrated" : formatDuration(estimatedRenderSeconds(generationSettings));
  const clipDuration = (generationSettings.frames / generationSettings.fps).toFixed(1);

  useEffect(() => {
    void useStudioStore.persist.rehydrate();
  }, []);

  function updateSetting(name: Exclude<keyof GenerationSettings, "model">, value: number) {
    setGenerationSettings((current) => ({ ...current, [name]: value }));
  }

  function updateImageSetting(name: Exclude<keyof ImageSettings, "quality">, value: number) {
    setImageSettings((current) => ({ ...current, [name]: value }));
  }

  function selectImageQuality(quality: ImageQuality, selectedRatio: AspectRatio = ratio) {
    const dimensions = {
      draft: { "1:1": [512, 512], "16:9": [768, 432], "9:16": [432, 768] },
      standard: { "1:1": [768, 768], "16:9": [1024, 576], "9:16": [576, 1024] },
      high: { "1:1": [1024, 1024], "16:9": [1152, 640], "9:16": [640, 1152] },
    } as const;
    const [width, height] = dimensions[quality][selectedRatio];
    setImageSettings((current) => ({ ...current, quality, width, height, steps: quality === "high" ? 8 : 4 }));
  }

  function selectModel(model: VideoModel) {
    setGenerationSettings((current) => ({ ...current, model, steps: model === "ltx-video" ? 8 : 12, cfg: model === "ltx-video" ? 1 : 5 }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const value = prompt.trim();
    if (!value || isGenerating) return;
    addMessage({ id: crypto.randomUUID(), role: "user", content: value, createdAt: new Date().toISOString() });
    if (mode === "image") {
      sendGeneration({ generation_type: "image", prompt: value, style: imageStyle, aspect_ratio: ratio, width: alignImageDimension(imageSettings.width), height: alignImageDimension(imageSettings.height), steps: imageSettings.steps, seed: imageSettings.seed }, imageStyle === "gif" ? "not calibrated" : "about 1 min");
    } else {
      sendGeneration({ generation_type: "video", prompt: value, aspect_ratio: ratio === "1:1" ? "16:9" : ratio, quality: "draft", ...generationSettings, reference_name: file?.name }, renderEstimate);
    }
    setPrompt("");
    setFile(null);
    if (ref.current) ref.current.style.height = "auto";
  }

  function selectMode(nextMode: GenerationMode) {
    setMode(nextMode);
    if (nextMode === "video" && ratio === "1:1") setRatio("16:9");
    if (nextMode === "image") selectImageQuality(imageSettings.quality, ratio === "1:1" ? "1:1" : ratio);
  }

  function selectRatio(nextRatio: AspectRatio) {
    setRatio(nextRatio);
    if (mode === "image") selectImageQuality(imageSettings.quality, nextRatio);
  }

  async function handleQueueAction(action: "cancel" | "clear-queue") {
    setQueueAction(action);
    try {
      await controlGeneration(action);
    } finally {
      setQueueAction(null);
    }
  }

  return <main className="studio">
    <aside className={drawer ? "sidebar open" : "sidebar"}>
      <div className="brand"><span><Clapperboard size={18} /></span>Framefoundry <button onClick={() => setDrawer(false)} aria-label="Close sessions"><X size={17} /></button></div>
      <button className="new" onClick={createSession}><Plus size={16} /> New Project</button>
      <p className="connection"><i className={connection === "connected" ? "on" : ""} />{connection === "connected" ? "Studio engine online" : "Engine offline"}</p>
      <p className="label"><History size={13} /> RECENT SESSIONS</p>
      {sessions.map((session) => <button className={session.id === currentSessionId ? "session active" : "session"} onClick={() => setCurrentSession(session.id)} key={session.id}>{session.title}<small>{session.updatedLabel}</small></button>)}
      <button className="clear" onClick={clearSessions}><Trash2 size={14} /> Clear history</button>
    </aside>
    {drawer && <button className="scrim" onClick={() => setDrawer(false)} aria-label="Close drawer" />}
    <section className="chat">
      <header>
        <button className="menu" onClick={() => setDrawer(true)} aria-label="Open sessions"><Menu size={19} /></button>
        <div><p className="label">PROJECT</p><h1>Untitled motion study</h1></div>
        <div className="header-actions">
          <button onClick={() => handleQueueAction("cancel")} disabled={!isGenerating || queueAction !== null} aria-label="Cancel current generation" title="Cancel current generation"><Ban size={18} /></button>
          <button onClick={() => handleQueueAction("clear-queue")} disabled={queueAction !== null} aria-label="Clear pending generations" title="Clear pending generations"><ListX size={18} /></button>
          <button onClick={() => setSettingsOpen(true)} aria-label="Studio settings" title="Studio settings"><SlidersHorizontal size={18} /></button>
        </div>
      </header>
      {settingsOpen && <div className="settings-backdrop" onMouseDown={() => setSettingsOpen(false)}>
        <section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title" onMouseDown={(event) => event.stopPropagation()}>
          <div className="settings-heading"><div><p className="label">GENERATION</p><h2 id="settings-title">Studio settings</h2></div><button onClick={() => setSettingsOpen(false)} aria-label="Close settings"><X size={18} /></button></div>
          {mode === "video" ? <>
            <div className="model-switch" role="group" aria-label="Generation model">
              <button className={generationSettings.model === "wan-2.1" ? "selected" : ""} onClick={() => selectModel("wan-2.1")}>Wan 2.1</button>
              <button className={generationSettings.model === "ltx-video" ? "selected" : ""} onClick={() => selectModel("ltx-video")}>LTX-Video 13B</button>
            </div>
            <div className="settings-grid">
              <label>Width<input type="number" min="16" step="2" value={generationSettings.width} onChange={(event) => updateSetting("width", Number(event.target.value))} /></label>
              <label>Height<input type="number" min="16" step="2" value={generationSettings.height} onChange={(event) => updateSetting("height", Number(event.target.value))} /></label>
              <label>Frames<input type="number" min="1" max="144" value={generationSettings.frames} onChange={(event) => updateSetting("frames", Number(event.target.value))} /></label>
              <label>Steps<input type="number" min="1" value={generationSettings.steps} onChange={(event) => updateSetting("steps", Number(event.target.value))} /></label>
              <label>CFG<input type="number" min="0.1" step="0.1" value={generationSettings.cfg} onChange={(event) => updateSetting("cfg", Number(event.target.value))} /></label>
              <label>FPS<input type="number" min="1" value={generationSettings.fps} onChange={(event) => updateSetting("fps", Number(event.target.value))} /></label>
              <label>Seed<input type="number" min="0" value={generationSettings.seed} onChange={(event) => updateSetting("seed", Number(event.target.value))} /></label>
            </div>
            <div className="settings-estimate"><span>Estimated render</span><strong>{generationSettings.model === "ltx-video" ? renderEstimate : `about ${renderEstimate}`}</strong><small>{clipDuration}s output · {generationSettings.model === "ltx-video" ? "calibrate after first run" : "M4 Pro benchmark"}</small></div>
          </> : <>
            <div className="model-switch" role="group" aria-label="Image quality">
              <button className={imageSettings.quality === "draft" ? "selected" : ""} onClick={() => selectImageQuality("draft")}>Draft</button>
              <button className={imageSettings.quality === "standard" ? "selected" : ""} onClick={() => selectImageQuality("standard")}>Standard</button>
              <button className={imageSettings.quality === "high" ? "selected" : ""} onClick={() => selectImageQuality("high")}>High</button>
            </div>
            <div className="settings-grid">
              <label>Width<input type="number" min="64" step="16" value={imageSettings.width} onChange={(event) => updateImageSetting("width", Number(event.target.value))} /></label>
              <label>Height<input type="number" min="64" step="16" value={imageSettings.height} onChange={(event) => updateImageSetting("height", Number(event.target.value))} /></label>
              <label>Steps<input type="number" min="1" max="12" value={imageSettings.steps} onChange={(event) => updateImageSetting("steps", Number(event.target.value))} /></label>
              <label>Seed<input type="number" min="0" value={imageSettings.seed} onChange={(event) => updateImageSetting("seed", Number(event.target.value))} /></label>
            </div>
            <div className="settings-estimate"><span>Image profile</span><strong>{imageStyle === "gif" ? "512x288 GIF profile" : `${imageSettings.width}x${imageSettings.height} · ${imageSettings.steps} steps`}</strong><small>{imageStyle === "gif" ? "LTX-Video 13B · 49 frames · 12 fps" : "FLUX.1 Schnell · MPS benchmark"}</small></div>
          </>}
          <div className="settings-footer"><button onClick={() => mode === "video" ? setGenerationSettings(defaultGenerationSettings) : setImageSettings(defaultImageSettings)}>Reset defaults</button><button className="apply-settings" onClick={() => setSettingsOpen(false)}>Done</button></div>
        </section>
      </div>}
      <div className="messages">
        <div className="welcome"><span><Sparkles size={21} /></span><p className="label">LOCAL VIDEO STUDIO</p><h2>Make the first frame count.</h2><p>Describe a shot, bring a reference, then shape every result in the timeline.</p><div className="ideas">{prompts.map((idea) => <button onClick={() => { setPrompt(idea); ref.current?.focus(); }} key={idea}>{idea}</button>)}</div></div>
        {messages.map((message) => message.role === "user" ? <div className="bubble" key={message.id}>{message.content}</div> : message.role === "progress" ? <div className="progress" key={message.id}><b>Generating media <em>{message.progress ?? 0}%</em></b><div><i style={{ width: `${message.progress ?? 0}%` }} /></div><p>{message.content}</p></div> : message.role === "video" && message.videoUrl ? <VideoViewer key={message.id} videoId={message.id} title={message.content} src={message.videoUrl} /> : message.role === "image" && message.imageUrl ? <ImageViewer key={message.id} title={message.content} src={message.imageUrl} /> : <p className="notice" key={message.id}>{message.content}</p>)}
      </div>
      <form onSubmit={submit} className="composer">
        {file && <div className="file"><ImagePlus size={13} />{file.name}<button type="button" onClick={() => setFile(null)} aria-label="Remove reference image"><X size={13} /></button></div>}
        <textarea ref={ref} rows={1} value={prompt} onChange={(event) => { setPrompt(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`; }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) submit(event); }} placeholder="Describe the next shot..." />
        <p className="render-summary">{mode === "image" ? `${imageStyle === "gif" ? "Animated GIF · LTX-Video 13B" : `Image · FLUX.1 Schnell · ${imageStyle}`} · ${ratio} · ${imageStyle === "gif" ? "512×288" : `${imageSettings.width}×${imageSettings.height} · ${imageSettings.steps} steps`}` : `${generationSettings.model === "ltx-video" ? "LTX-Video 13B" : "Wan 2.1"} · ${generationSettings.width}×${generationSettings.height} · ${generationSettings.frames} frames · ${generationSettings.steps} steps · ${generationSettings.model === "ltx-video" ? renderEstimate : `about ${renderEstimate}`}`}</p>
        <div><div className="generation-mode" role="group" aria-label="Generation type"><button type="button" className={mode === "image" ? "selected" : ""} onClick={() => selectMode("image")} title="Generate image"><Images size={15} />Image</button><button type="button" className={mode === "video" ? "selected" : ""} onClick={() => selectMode("video")} title="Generate video"><Video size={15} />Video</button></div>{mode === "image" ? <label className="style-select">{imageStyle === "3d" ? "3D" : imageStyle[0].toUpperCase() + imageStyle.slice(1)}<ChevronDown size={13} /><select value={imageStyle} onChange={(event) => setImageStyle(event.target.value as ImageStyle)}><option value="photo">Photo</option><option value="3d">3D render</option><option value="graphic">Graphic</option><option value="art">Art</option><option value="gif">Animated GIF</option></select></label> : <label><ImagePlus size={16} /><span>Upload Reference Image</span><input type="file" accept="image/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>}<label className="ratio">{ratio}<ChevronDown size={13} /><select value={ratio} onChange={(event) => selectRatio(event.target.value as AspectRatio)}>{mode === "image" && <option>1:1</option>}<option>16:9</option><option>9:16</option></select></label><button className="send" disabled={!prompt.trim() || isGenerating} aria-label="Send"><Send size={16} /></button></div>
      </form>
    </section>
  </main>;
}
