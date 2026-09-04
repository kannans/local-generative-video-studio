"use client";

import { Download } from "lucide-react";

type ImageViewerProps = {
  src: string;
  title: string;
};

export function ImageViewer({ src, title }: ImageViewerProps) {
  return <figure className="image-viewer">
    {/* Generated media can be served from the local backend rather than Next.js. */}
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={src} alt={title} />
    <figcaption><span>{title}</span><a href={src} download title="Download image" aria-label="Download image"><Download size={16} /></a></figcaption>
  </figure>;
}
