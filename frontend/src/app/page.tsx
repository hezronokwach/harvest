"use client";

import React, { useState } from "react";
import EmotionRadar from "@/components/EmotionRadar";
import PriceGapSlider from "@/components/PriceGapSlider";
import InnerMonologue from "@/components/InnerMonologue";

export default function Home() {
  const [jumaEmotions, setJumaEmotions] = useState({
    confidence: 0.85,
    stress: 0.12,
    urgency: 0.45,
  });

  const [alexEmotions, setAlexEmotions] = useState({
    confidence: 0.62,
    stress: 0.78,
    urgency: 0.92,
  });

  const [negotiation, setNegotiation] = useState({
    ask: 1.25,
    bid: 0.95,
    target: 1.15,
  });

  const [thoughts, setThoughts] = useState([
    {
      id: "1",
      agent: "Alex",
      text: "I have a strict budget of $1.00, but the deadline is in 2 hours. I might have to bend.",
      type: "strategy" as const,
    },
    {
      id: "2",
      agent: "Juma",
      text: "Alex sounds hurried. I'm holding the anchor at $1.25 for another round.",
      type: "strategy" as const,
    },
    {
      id: "3",
      agent: "Juma",
      text: "Detecting high stress in Buyer's vocal tone. Mirroring silence to force a move.",
      type: "insight" as const,
    },
  ]);

  return (
    <main className="min-h-screen bg-[#050505] text-white p-8 font-sans selection:bg-orange-500/30">
      {/* Header */}
      <div className="max-w-7xl mx-auto flex justify-between items-center mb-12 border-b border-white/5 pb-8">
        <div>
          <h1 className="text-3xl font-black tracking-tighter uppercase italic flex items-center gap-2">
            <span className="text-orange-500">Echo</span>Yield
            <span className="bg-orange-500 text-black text-[10px] px-1.5 py-0.5 not-italic tracking-normal rounded font-bold">ALPHA</span>
          </h1>
          <p className="text-gray-500 text-xs mt-1 uppercase tracking-widest">Farmer's Strategic Negotiation Hub</p>
        </div>
        <div className="flex gap-4">
          <div className="text-right">
            <p className="text-[10px] text-gray-600 uppercase">LiveKit Room</p>
            <p className="text-xs font-mono text-green-500 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" /> BARN_ROOM_01
            </p>
          </div>
          <button className="bg-orange-500 hover:bg-orange-600 px-6 py-2 rounded-lg text-sm font-bold text-black transition-all">
            MANUAL OVERRIDE
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto grid grid-cols-12 gap-8">
        {/* Left Column: Negotiation Visuals */}
        <div className="col-span-8 space-y-8">
          {/* Price Controller */}
          <PriceGapSlider
            ask={negotiation.ask}
            bid={negotiation.bid}
            target={negotiation.target}
          />

          {/* Emotion Layout */}
          <div className="grid grid-cols-2 gap-8">
            <EmotionRadar agentName="Juma (Seller)" scores={jumaEmotions} color="#f97316" />
            <EmotionRadar agentName="Alex (Buyer)" scores={alexEmotions} color="#3b82f6" />
          </div>

          {/* Connection Status / Visualizer Placeholder */}
          <div className="h-48 rounded-xl bg-gradient-to-t from-orange-500/5 to-transparent border border-white/5 flex items-center justify-center">
            <div className="flex gap-1 items-center">
              {[...Array(20)].map((_, i) => (
                <div
                  key={i}
                  className="w-1 bg-orange-500/40 rounded-full animate-bounce"
                  style={{
                    height: `${Math.random() * 60 + 20}%`,
                    animationDelay: `${i * 0.1}s`
                  }}
                />
              ))}
              <p className="mx-4 text-xs font-mono text-gray-500 uppercase">Negotiation Audio Stream Active</p>
              {[...Array(20)].map((_, i) => (
                <div
                  key={i}
                  className="w-1 bg-blue-500/40 rounded-full animate-bounce"
                  style={{
                    height: `${Math.random() * 60 + 20}%`,
                    animationDelay: `${i * 0.1}s`
                  }}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Right Column: Strategic Thoughts */}
        <div className="col-span-4">
          <InnerMonologue thoughts={thoughts} />

          <div className="mt-8 p-6 rounded-xl bg-orange-500 text-black">
            <h4 className="text-xs font-black uppercase mb-1">Current Directive</h4>
            <p className="text-lg font-bold leading-tight italic">"Secure $1.15. Alex is bluffing about the second supplier."</p>
            <div className="mt-4 flex gap-2">
              <span className="text-[10px] font-bold border border-black/20 px-2 py-0.5 rounded">TACTICAL EMPATHY</span>
              <span className="text-[10px] font-bold border border-black/20 px-2 py-0.5 rounded">ANCHORING</span>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
