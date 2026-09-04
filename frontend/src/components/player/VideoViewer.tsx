"use client";

import { PointerEvent, useEffect, useRef, useState } from "react";
import { Download, Eraser, Film, Music2, Pause, Play, Repeat2, TimerReset } from "lucide-react";
import { MaskStroke, resolveVideoUrl, useStudioStore } from "@/lib/store";

interface VideoViewerProps { videoId: string; src: string; title: string }
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
const initialPlayback = { playing: false, loop: false, currentTime: 0, duration: 0 };
const emptyMasks: MaskStroke[] = [];

export function VideoViewer({ videoId, src, title }: VideoViewerProps) {
  const videoRef = useRef<HTMLVideoElement>(null); const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoUrl = resolveVideoUrl(src) ?? src;
  const [brush, setBrush] = useState(false); const [drawing, setDrawing] = useState(false);
  const playback = useStudioStore((state) => state.playback[videoId] ?? initialPlayback);
  const masks = useStudioStore((state) => state.masks[videoId] ?? emptyMasks); const setPlayback = useStudioStore((state) => state.setPlayback); const setMask = useStudioStore((state) => state.setMask);
  useEffect(() => { const canvas = canvasRef.current; const context = canvas?.getContext("2d"); if (!canvas || !context) return; context.clearRect(0, 0, canvas.width, canvas.height); context.strokeStyle = "rgba(239,108,79,.85)"; context.lineCap = "round"; masks.forEach((stroke) => { context.lineWidth = stroke.size; context.beginPath(); stroke.points.forEach((p, index) => index ? context.lineTo(p.x * canvas.width, p.y * canvas.height) : context.moveTo(p.x * canvas.width, p.y * canvas.height)); context.stroke(); }); }, [masks]);
  function togglePlay() { const video = videoRef.current; if (!video) return; if (video.paused) { video.play(); setPlayback(videoId, { playing: true }); } else { video.pause(); setPlayback(videoId, { playing: false }); } }
  function position(event: PointerEvent<HTMLCanvasElement>) { const bounds = event.currentTarget.getBoundingClientRect(); return { x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height }; }
  function start(event: PointerEvent<HTMLCanvasElement>) { if (!brush) return; videoRef.current?.pause(); setPlayback(videoId, { playing: false }); setDrawing(true); setMask(videoId, [...masks, { points: [position(event)], size: 26 }]); event.currentTarget.setPointerCapture(event.pointerId); }
  function paint(event: PointerEvent<HTMLCanvasElement>) { if (!drawing || !brush) return; const next = [...masks]; next[next.length - 1] = { ...next[next.length - 1], points: [...next[next.length - 1].points, position(event)] }; setMask(videoId, next); }
  function download() { const link = document.createElement("a"); link.href = videoUrl; link.download = `${title}.mp4`; link.click(); }
  return <article className="viewer"><div className="viewer-top"><span><Film size={15} /> {title}</span><span>420p</span></div><div className="video-stage"><video ref={videoRef} src={videoUrl} loop={playback.loop} playsInline onLoadedMetadata={(event) => setPlayback(videoId, { duration: event.currentTarget.duration })} onTimeUpdate={(event) => setPlayback(videoId, { currentTime: event.currentTarget.currentTime })} onEnded={() => setPlayback(videoId, { playing: false })} /><canvas ref={canvasRef} className={brush ? "mask-canvas active" : "mask-canvas"} width={746} height={420} onPointerDown={start} onPointerMove={paint} onPointerUp={() => setDrawing(false)} /><button className="play-overlay" onClick={togglePlay} aria-label="Toggle playback">{playback.playing ? <Pause size={22} fill="currentColor" /> : <Play size={22} fill="currentColor" />}</button>{brush && <span className="brush-note"><Eraser size={14} /> Paint object mask</span>}</div><div className="player-controls"><button onClick={togglePlay} aria-label="Toggle playback">{playback.playing ? <Pause size={16} /> : <Play size={16} />}</button><input aria-label="Scrub video" type="range" min="0" max={playback.duration || 0} value={playback.currentTime} onChange={(event) => { if (videoRef.current) videoRef.current.currentTime = Number(event.target.value); }} /><span>{clock(playback.currentTime)} / {clock(playback.duration)}</span><button className={playback.loop ? "selected" : ""} onClick={() => setPlayback(videoId, { loop: !playback.loop })} aria-label="Loop video"><Repeat2 size={16} /></button></div><div className="quick-actions"><button><TimerReset size={15} /> Extend +5s</button><button><Music2 size={15} /> Add Audio</button><button className={brush ? "active-action" : ""} onClick={() => setBrush(!brush)}><Eraser size={15} /> {brush ? "Finish Mask" : "Remove Masked Object"}</button><button onClick={download}><Download size={15} /> Download MP4</button></div></article>;
}
