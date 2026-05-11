"use client";

import { CommandPalette } from "../../components/command-palette";

/** Client-component mount point for the cmd-K palette. Keeps the parent layout server-side. */
export default function PaletteMount() {
  return <CommandPalette />;
}
