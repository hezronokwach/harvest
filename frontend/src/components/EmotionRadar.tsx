"use client";

import React from "react";

interface EmotionScores {
  confidence: number;
  stress: number;
  urgency: number;
}

interface EmotionRadarProps {
  agentName: string;
  scores: EmotionScores;
  color: string;
}

export default function EmotionRadar({ agentName, scores, color }: EmotionRadarProps) {
  // Simple visualization for now: progress bars representing the hex-map concept
  return (
    <div className="p-4 rounded-xl bg-gray-900/50 border border-gray-800 backdrop-blur-md">
      <h3 className="text-sm font-medium text-gray-400 mb-4 tracking-wider uppercase">{agentName} Emotions</h3>
      <div className="space-y-4">
        <EmotionBar label="Confidence" value={scores.confidence} color={color} />
        <EmotionBar label="Stress" value={scores.stress} color="#ef4444" />
        <EmotionBar label="Urgency" value={scores.urgency} color="#eab308" />
      </div>
    </div>
  );
}

function EmotionBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs font-mono">
        <span className="text-gray-500">{label}</span>
        <span style={{ color }}>{Math.round(value * 100)}%</span>
      </div>
      <div className="h-1.5 w-full bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full transition-all duration-500 ease-out"
          style={{ width: `${value * 100}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}
